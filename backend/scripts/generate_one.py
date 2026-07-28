"""跑一筆 generate 並印結果。

執行（須在 backend/ 下，DATABASE_URL=... 從 .env 讀）：
    uv run python -m scripts.generate_one --topic "量子計算"
    uv run python -m scripts.generate_one --topic "區塊鏈基礎" --angle "應用"
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import date
from pathlib import Path

from psycopg.rows import dict_row

from engine.pipeline.generate_job import run_generate_job
from shared.config import get_settings
from shared.db.pool import close_pool, connection

logger = logging.getLogger(__name__)


async def _fetch_slug(episode_id: str) -> str:
    async with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        await cur.execute("select slug from public.episodes where id = %s", (episode_id,))
        row = await cur.fetchone()
    if row is None:
        raise RuntimeError(f"找不到 episode {episode_id}")
    slug: str = row["slug"]
    return slug


async def _resynth_real_audio(episode_id: str) -> None:
    """--mock 產生的 episode 音檔是 MockRenderer 的 64-byte 假檔（見
    engine/pipeline/langgraph_pod/mock.py:MockRenderer.render），前端
    decodeAudioData 會直接失敗。這裡用真 TTS 重新合成，蓋掉假音檔 + cues
    （兩者必須來自同一次 render，時間軸才對得齊——見方案 B 核心保證）。

    走真正的 shared.storage.r2 模組（非 MockR2）：該模組在 ENVIRONMENT=dev
    且沒填 R2 金鑰時會自動落地本機檔案 + 簽 /mock-r2/{key} URL，跟 production
    router／backfill 走同一份程式碼，不用另外維護一份 mock 上傳邏輯。
    """
    import shutil

    from engine.media import make_job_workdir, render_episode
    from engine.pipeline import reuse_repo as db_repo
    from shared.models.engine import ScriptJSON
    from shared.storage import r2

    async with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "select script_json, cefr_level from public.episodes where id = %s", (episode_id,)
        )
        row = await cur.fetchone()
    if row is None:
        raise RuntimeError(f"找不到 episode {episode_id}")
    script_json = dict(row["script_json"])
    script_json.pop("cues", None)  # ScriptJSON 沒有 cues 欄位，是 update_episode_keys 另外併進去的
    script = ScriptJSON.model_validate(script_json)

    workdir = make_job_workdir()
    try:
        artifacts = await render_episode(script, workdir, cefr=row.get("cefr_level") or "B1")
        keys: list[str] = []
        for seg in artifacts.segments:
            key = f"episodes/{episode_id}/segments/{seg.index:03d}.mp3"
            r2.put_object(key, seg.audio_path.read_bytes(), "audio/mpeg")
            keys.append(key)
        await db_repo.update_episode_keys(
            episode_id,
            audio_keys=keys,
            script_json=script.model_dump(by_alias=False),
            cues=artifacts.cues,
        )
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


async def main(
    topic: str, angle: str, topic_type: str, length_tier: str,
    user_id: str | None, use_mock: bool,
) -> None:
    cfg = get_settings()
    # 沒指定 --user-id 時預設用 DEV_USER_ID：is_free 預設 false，episodes 要有 deliveries
    # 列才會出現在該 user 的首頁；空 user_ids 會生出「誰都看不到」的孤兒集數。
    resolved_uid = user_id or cfg.dev_user_id or None
    body = {
        "big_topic": topic,
        "canonical_topic": topic,
        "angle": angle,
        "topic_type": topic_type,
        "deliver_date": date.today().isoformat(),
        "user_ids": [resolved_uid] if resolved_uid else [],
        "length_tier": length_tier,
    }
    run_kwargs: dict[str, object] = {}
    if use_mock:
        import json

        from engine.pipeline import reuse_repo as db_repo
        from engine.pipeline.langgraph_pod.chat import FakeChatModel
        from engine.pipeline.langgraph_pod.mock import (
            MockRenderer,
            get_mocks,
            make_mock_workdir,
        )
        from shared.models.engine import ScriptJSON

        # 寫稿流程是「outline 1 次 + N 段」（見 nodes.py:_invoke_writer），不是單次
        # LLM 呼叫。用 tests/fixtures/loop_engineering.json（真實驗證過的合法
        # ScriptJSON）當範本，鏡射 test_pipeline.py::_patch_generate_job 的組法：
        # 1 outline（3 段）+ 3 個一樣的 segment 回應，judge 直接過（不測 rewrite loop）。
        fixture_path = (
            Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "loop_engineering.json"
        )
        script = ScriptJSON.model_validate_json(fixture_path.read_text(encoding="utf-8"))
        outline_json = json.dumps({
            "topic": topic,
            "topic_zh": script.topic_zh,
            "category": script.category,
            "extracted_facts": [f.model_dump() for f in script.extracted_facts],
            "target_vocab": [v.model_dump() for v in script.target_vocab],
            "segments": [{"focus": f"Part {i + 1}", "vocab_words": []} for i in range(3)],
        })
        segment_json = json.dumps({"script": [line.model_dump() for line in script.script]})
        passing_judge = json.dumps({
            "hook_strength": 0.8, "informativeness": 0.8, "pacing": 0.8,
            "chemistry": 0.8, "groundedness": 0.8, "feedback": [],
        })
        chat = FakeChatModel(
            responses=[outline_json] + [segment_json] * 3,
            judge_responses=[passing_judge],
        )
        # 真實 DB repo（讓新集落 dev DB，前端可 GET）；r2 / queue / renderer 走
        # in-memory mock（render/upload 這輪產的是假音檔，_resynth_real_audio
        # 隨後會用真 TTS 整個蓋掉，這裡的假上傳結果不會被用到）。
        _, _, queue = get_mocks(reset=True)
        run_kwargs.update({
            "chat": chat,
            "chat_failover": None,
            "repo": db_repo,
            "queue": queue,
            "renderer": MockRenderer(make_mock_workdir()),
        })
    episode_id = await run_generate_job(body, cfg, **run_kwargs)
    if episode_id is None:
        await close_pool()
        print("✗ storage 上傳失敗，優雅結束（row 已清），沒有集數產出")
        return
    try:
        if use_mock:
            print("→ LLM/DB 用 mock 完成，正在用真 TTS 重新合成音檔...")
            await _resynth_real_audio(episode_id)
        slug = await _fetch_slug(episode_id)
    finally:
        await close_pool()
    print(f"✓ episode_id={episode_id}  slug={slug}")
    print(f"  Player:  http://localhost:5173/player/{slug}")
    if use_mock:
        print(f"  音檔本地路徑: /tmp/dc_mock_r2/episodes/{episode_id}/segments/")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser(description="產生一集 podcast 並落庫")
    p.add_argument("--topic", required=True, help="集數主題（會做 slug）")
    p.add_argument("--angle", default="定義", help="切入角度（預設：定義）")
    p.add_argument(
        "--topic-type",
        default="evergreen",
        choices=["news", "product", "evergreen", "skill"],
        help="入口類型：news=今日新聞 / product=指定主題 / evergreen=深度知識（預設：evergreen）",
    )
    p.add_argument(
        "--length-tier",
        default="medium",
        choices=["short", "medium", "long"],
        help="長度 tier（預設：medium）",
    )
    p.add_argument(
        "--user-id",
        default=None,
        help="收件 user_id，會 insert deliveries 讓該 user 首頁看得到（預設：.env 的 DEV_USER_ID）",
    )
    p.add_argument(
        "--mock",
        action="store_true",
        help="走 in-memory mock（無需 LLM API key；segments 寫到 /tmp/dc_mock_r2/）",
    )
    args = p.parse_args()
    asyncio.run(
        main(args.topic, args.angle, args.topic_type, args.length_tier, args.user_id, args.mock)
    )
