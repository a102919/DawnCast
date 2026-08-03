"""Episode 收尾階段 nodes：upsert → render → upload → update_keys → deliveries → backfill_dict。

R2 上傳/儲存失敗一律 graceful END（`storage_decision` → `dead_letter_node`），
不 raise——避免 worker pgmq vt 重投讓 33s+ 的 render_episode 整個重做。
"""

from __future__ import annotations

import logging
import re
import shutil
import uuid
from typing import Any, Literal

from langchain_core.runnables import RunnableConfig
from psycopg.errors import ForeignKeyViolation

from engine.media import EpisodeArtifacts, make_job_workdir, render_episode
from shared.db import repo as app_repo
from shared.idempotency import compute_idempotency_key
from shared.models import Cue, ScriptJSON
from shared.push import notify_user

from .nodes_common import _collector, _ctx
from .state import PodState

logger = logging.getLogger(__name__)


def _slugify(canonical: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "_", canonical.casefold()).strip("_")
    base = base[:40] or "episode"
    return f"{base}_{uuid.uuid4().hex[:8]}"


def _source_metadata(state: PodState) -> list[dict[str, Any]]:
    """持久化來源 attribution；不把原文 text 寫進 episodes.sources。"""
    return [
        {
            "id": source.id,
            "title": source.title,
            "url": source.url,
            "provider": source.source or "",
            "source_type": "",
            "published_at": source.published_at,
        }
        for source in state.get("sources", [])
    ]


async def upsert_episode_node(state: PodState, config: RunnableConfig) -> dict[str, Any]:
    ctx = _ctx(config)
    repo = ctx["repo"]
    collector = _collector(config)

    script: ScriptJSON = state["script"]
    cluster_id = state.get("cluster_id")
    deliver_date = state["deliver_date"]
    big_topic = state["big_topic"]
    angle = state["angle"]
    canonical = state["canonical_topic"]
    length_tier = state.get("length_tier") or "medium"
    topic_type = state.get("topic_type") or "evergreen"
    cefr = state.get("cefr") or "B1"
    source = state.get("source") or "fallback"
    is_free = source != "specified"
    channel_id = state.get("channel_id")
    channel_topic_id = state.get("channel_topic_id")

    # format 是 derived（=resolve_format(topic_type, length_tier)），不重複併入 idem_key。
    # channel_id 同理絕對不可進 idem_key：canonical_topic 已足以區分內容，把 channel
    # 加進 key 會讓同一個題目在不同頻道被視為「不同集」而重複完整生成一次，白白
    # 浪費 LLM 與 TTS 配額（頻道只是這集的「歸屬／編號」屬性，不是內容維度）。
    idem_key = compute_idempotency_key(
        cluster_id=cluster_id,
        deliver_date=deliver_date,
        big_topic=big_topic,
        angle=angle,
        length_tier=length_tier,
        topic_type=topic_type,
    )
    slug = _slugify(canonical)
    script_format = state.get("format", "dialogue")
    grounded = bool(state.get("grounded"))

    usage_log = state.get("token_usage") or []
    total_in = sum(int(u.get("input_tokens", 0)) for u in usage_log)
    total_out = sum(int(u.get("output_tokens", 0)) for u in usage_log)

    # 屬於某個頻道 → 先取頻道內流水號（不屬於任何頻道時完全跳過，維持既有個人化
    # 生成路徑零開銷）。next_episode_no 失敗刻意不 try/except：這是要落庫的真實
    # 資料，不是可事後補的次要資訊，失敗就該讓整條 graph 照現有重試機制處理。
    episode_no: int | None = None
    if channel_id is not None:
        from shared.db import channels  # noqa: PLC0415 lazy import（頻道機制專用）

        episode_no = await channels.next_episode_no(channel_id)

    # repo 是 MockRepo 或 shared.db.repo 模組，surface 相同——直接呼叫，不做 hasattr 分派。
    episode_id, already_rendered = await repo.upsert_episode(
        idempotency_key=idem_key,
        slug=slug,
        title=script.topic,
        topic=script.category,
        big_topic=big_topic,
        angle=angle,
        topic_type=state["topic_type"],
        cefr_level=cefr,
        title_zh=script.topic_zh,
        cluster_id=cluster_id,
        length_tier=length_tier,
        format=script_format,
        grounded=grounded,
        input_tokens=total_in,
        output_tokens=total_out,
        is_free=is_free,
        sources=_source_metadata(state),
        generation_started_at=collector.started_at if collector is not None else None,
        gen_metrics=collector.gen_metrics() if collector is not None else None,
        research_metrics=collector.research_metrics() if collector is not None else None,
        channel_id=channel_id,
        episode_no=episode_no,
    )

    run_id = ctx.get("pipeline_run_id")
    if run_id is not None and not already_rendered:
        await repo.attach_pipeline_run_episode(run_id, episode_id)

    # 頻道選題回填：只在「這次真的新產出」時才回填（already_rendered=True 代表撞到
    # 既有集，選題狀態 / 頻道進度早該在第一次成功時就已回填過，這裡不重複做）。
    # 兩個回填呼叫都必須容錯——這集已經產出來了，回填失敗是「選題庫狀態沒更新」
    # 這種可事後修的次要問題，不可讓它拖垮整條已經成功的 graph（同 2026-07-20
    # FK violation 死循環教訓：compensation / 回填一律 try/except 記 warning 就好）。
    if not already_rendered:
        if channel_topic_id is not None:
            try:
                from shared.db import channels  # noqa: PLC0415 lazy import

                await channels.update_topic_status(
                    channel_topic_id, "published", episode_id=episode_id
                )
            except Exception:
                logger.warning(
                    "頻道選題狀態回填失敗（不影響本集產出）channel_topic_id=%s episode_id=%s",
                    channel_topic_id,
                    episode_id,
                    exc_info=True,
                )
        if channel_id is not None:
            try:
                from shared.db import channels  # noqa: PLC0415 lazy import

                await channels.mark_channel_published(channel_id, deliver_date)
            except Exception:
                logger.warning(
                    "頻道出版狀態回填失敗（不影響本集產出）channel_id=%s deliver_date=%s",
                    channel_id,
                    deliver_date,
                    exc_info=True,
                )

    if usage_log:
        logger.info(
            "generate token 用量 episode_id=%s big_topic=%s input=%d output=%d total=%d calls=%d",
            episode_id,
            big_topic,
            total_in,
            total_out,
            total_in + total_out,
            len(usage_log),
        )

    return {
        "episode_id": episode_id,
        "slug": slug,
        "idempotency_key": idem_key,
        "already_rendered": already_rendered,
    }


def render_branch_decision(state: PodState) -> Literal["render", "deliveries"]:
    return "deliveries" if state.get("already_rendered") else "render"


# ── Node 6: render_episode ────────────────────────────────


async def render_episode_node(state: PodState, config: RunnableConfig) -> dict[str, Any]:
    ctx = _ctx(config)
    renderer = ctx.get("renderer")  # None → 用 production render_episode

    script: ScriptJSON = state["script"]

    if renderer is not None:
        # mock 路徑
        from .mock import MockRenderer, make_mock_workdir  # noqa: PLC0415

        workdir = make_mock_workdir()
        if not isinstance(renderer, MockRenderer):
            raise TypeError("renderer 不是 MockRenderer")
        script_payload = script.model_dump()
        segments, srt, cues, mp3_path = renderer.render(script_payload)
        return {
            "artifacts": EpisodeArtifacts(
                segments=segments,
                srt=srt,
                vtt="",  # mock 不產
                cues=[Cue(**c) for c in cues],
                mp3_path=mp3_path,
            ),
        }

    # production 路徑
    # workdir 不能用 auto-cleanup 的 TemporaryDirectory：每行 mp3 檔要活到
    # upload_artifacts_node（下一個 node）讀完才能刪，見 upload_artifacts_node 的 cleanup。
    workdir = make_job_workdir()
    artifacts = await render_episode(script, workdir, cefr=state.get("cefr") or "B1")
    collector = _collector(config)
    if collector is not None:
        collector.record_tts_usage(
            provider=artifacts.tts_provider, characters=artifacts.tts_characters
        )
    return {"artifacts": artifacts}


# ── Node 7: upload_artifacts ──────────────────────────────


def storage_decision(
    state: PodState, config: RunnableConfig
) -> Literal["update_keys", "dead_letter"]:
    """upload_artifacts 後分流：storage_failed → dead_letter_node → END，否則 update_keys。

    R2 上傳失敗不能留半完成 row（播放頁不能拿同 slug 舊檔冒充新音檔），也不能
    raise（會觸發 worker pgmq vt 重投 → render 整個重做）。改成 graceful END：
    decision 走 dead_letter_node 做 DELETE + 寫 errors，worker 視為完成，
    read_ct 不累積。
    """
    if not state.get("storage_failed"):
        return "update_keys"
    return "dead_letter"


async def dead_letter_node(state: PodState, config: RunnableConfig) -> dict[str, Any]:
    """storage_failed + 無本地 fallback → DELETE 半完成 row，graceful END。

    取代原本 update_episode_keys_node 在這情況 raise RuntimeError 的行為：
    raise 會觸發 LangGraph 整個 invoke 失敗 → worker pgmq vt 重投 → 整集
    render_episode (TTS 33s+) 重做。改 graceful END：DELETE row + 寫
    errors 標記，worker 視為完成，read_ct 不累積。

    回傳 dict 必須明確把 episode_id 清成 None：LangGraph 對沒回傳的 key 會保留
    舊值，這裡的 row 已經被 DELETE，state 裡若還留著那個 uuid，run_pod() 的
    `final.get("episode_id")` truthy 檢查就會誤判成功，把已刪除的 episode_id
    當結果回傳。
    """
    ctx = _ctx(config)
    repo = ctx.get("repo")
    idem_key = state.get("idempotency_key")
    slug = state.get("slug")
    episode_id = state.get("episode_id")
    if repo is not None and idem_key:
        await repo.delete_episode_by_idem(idem_key)
    collector = _collector(config)
    run_id = ctx.get("pipeline_run_id")
    if collector is not None:
        collector.finalize("dead_letter")
        if repo is not None and run_id is not None:
            await repo.finalize_pipeline_run(
                run_id,
                status="dead_letter",
                gen_metrics=collector.gen_metrics(),
                research_metrics=collector.research_metrics(),
            )
    logger.warning(
        "媒體雙重失敗 graceful dead-letter（id=%s slug=%s idem=%s）",
        episode_id,
        slug,
        idem_key,
    )
    return {
        "episode_id": None,
        "errors": [
            *state.get("errors", []),
            f"upload_artifacts 雙重失敗，row 已清。slug={slug}",
        ],
    }


async def upload_artifacts_node(state: PodState, config: RunnableConfig) -> dict[str, Any]:
    ctx = _ctx(config)
    r2 = ctx.get("r2")

    episode_id = state["episode_id"]
    art: EpisodeArtifacts = state["artifacts"]

    prefix = f"episodes/{episode_id}"
    srt_key = f"{prefix}/episode.srt"
    mp3_key = f"{prefix}/episode.mp3"

    is_production = r2 is None  # mock 路徑會注入 MockR2；production 沒有才走真 R2

    storage_failed = False
    mp3_uploaded = False

    if r2 is not None:
        client = r2
    else:
        from shared.storage import r2 as real_r2  # noqa: PLC0415

        client = real_r2

    # Phase 4：整集 mp3 接管前端播放，segments/sidecar 停產。
    try:
        client.put_object(mp3_key, art.mp3_path.read_bytes(), "audio/mpeg")
        mp3_uploaded = True
        client.put_object(srt_key, art.srt.encode("utf-8"), "application/x-subrip")
    except Exception as exc:  # 包括 StorageError 與 MockR2 forced failure
        logger.warning(
            "upload_artifacts 失敗（%s）episode_id=%s",
            exc,
            episode_id,
        )
        if mp3_uploaded:
            # mp3 傳成功但 srt 傳失敗：dead_letter_node 只刪 DB row，不清 R2，
            # 不主動刪這裡上傳的 mp3 會變成永久孤兒物件。
            try:
                client.delete_object(mp3_key)
            except Exception:
                logger.warning(
                    "孤兒 mp3 清理失敗 episode_id=%s key=%s", episode_id, mp3_key, exc_info=True
                )
        mp3_uploaded = False
        storage_failed = True

    # render_episode_node 用 make_job_workdir()（不會自動清）產出這些檔案，
    # 讀完就是清掉的時機。
    if is_production and art.segments:
        shutil.rmtree(art.segments[0].audio_path.parent, ignore_errors=True)

    # audio_keys 保留向後相容（介面穩定；update_episode_keys 不再用，Phase 4+1
    # 再決定是否從 PodState 與回傳 dict 拔掉）。
    return {
        "audio_keys": [],
        "mp3_key": mp3_key if mp3_uploaded and not storage_failed else None,
        "srt_key": srt_key if not storage_failed else None,
        "storage_failed": storage_failed,
    }


# ── Node 8: update_episode_keys ───────────────────────────


async def update_episode_keys_node(state: PodState, config: RunnableConfig) -> dict[str, Any]:
    """storage_decision 只在 `not storage_failed` 時才會路由到這裡（見 graph.py），
    storage_failed=True 一律走 dead_letter_node → END，這裡不需要再處理雙重失敗分支。
    """
    ctx = _ctx(config)
    repo = ctx["repo"]
    collector = _collector(config)
    run_id = ctx.get("pipeline_run_id")
    art: EpisodeArtifacts = state["artifacts"]
    script: ScriptJSON = state["script"]
    # extracted_facts 現在是 SourcedFact 物件（非純字串），jsonb 落庫前先轉 dict。
    facts_payload = [f.model_dump(by_alias=False) for f in script.extracted_facts]

    if collector is not None:
        collector.finalize("success")

    # repo 是 MockRepo 或 shared.db.repo 模組，surface 相同——直接呼叫，不做 hasattr 分派。
    await repo.update_episode_keys(
        state["episode_id"],
        audio_key=state.get("mp3_key"),
        srt_key=state.get("srt_key"),
        script_json=script.model_dump(by_alias=False),
        cues=art.cues,
        extracted_facts=facts_payload,
        target_vocab=[v.model_dump(by_alias=False) for v in script.target_vocab],
        sources=_source_metadata(state),
        generation_finished_at=collector.finished_at if collector is not None else None,
        gen_metrics=collector.gen_metrics() if collector is not None else None,
    )
    if collector is not None and run_id is not None:
        await repo.finalize_pipeline_run(
            run_id,
            status="success",
            gen_metrics=collector.gen_metrics(),
            research_metrics=collector.research_metrics(),
        )
    return {}


# ── Node 9: insert_deliveries ─────────────────────────────


async def insert_deliveries_node(state: PodState, config: RunnableConfig) -> dict[str, Any]:
    ctx = _ctx(config)
    repo = ctx["repo"]

    user_ids: list[str] = state.get("user_ids") or []
    episode_id = state["episode_id"]
    deliver_date = state["deliver_date"]
    order_id = state.get("order_id")

    for uid in user_ids:
        try:
            # insert_delivery 回傳「是否首次寫入」，直接當推送的去重閘門——
            # pipeline 重投時 ON CONFLICT DO NOTHING 回 False，不會重複通知。
            # order_id：個人點餐路徑才有值（user_ids 恆為單一使用者）；
            # 頻道批次路徑 order_id 是 None，行為與改動前一致。
            #
            # 個人點餐路徑走 app_repo.deliver_and_mark_ready：把 delivery 寫入
            # 跟 queued→ready 翻牌收進同一個 transaction，避免前端輪詢踩到
            # 「集數已交付但訂單仍 queued」的不一致窗口（race + 翻牌缺失會把
            # activeOrder 卡死直到 DB 真翻 ready 前都救不回）。
            if order_id is not None:
                inserted = await app_repo.deliver_and_mark_ready(
                    uid,
                    episode_id,
                    deliver_date,
                    order_id=order_id,
                )
            else:
                inserted = await repo.insert_delivery(
                    uid,
                    episode_id,
                    deliver_date,
                    order_id=order_id,
                )
        except ForeignKeyViolation:
            # 上游補償（update_episode_keys_node 的 DELETE-on-failure 或 worker
            # _compensate_generate_failure）已把這筆 episode row 刪掉 —
            # 沒對應 row 就沒人可以交付，當作「這集本輪失敗、不交付」，
            # 不讓 FK violation 終止整個 graph（否則 graph 失敗 → worker 走
            # vt-retry → 又卡同一個 FK → 死循環）。
            logger.warning(
                "insert_delivery 找不到對應 episode（id=%s uid=%s），上輪補償刪掉了，略過",
                episode_id,
                uid,
            )
            continue

        if not inserted:
            continue

        # 拿這集的對外資訊（slug + 中文標題）拼通知 payload。get_episode_meta
        # 回 None 表示 episode 已不存在（FK CASCADE 理論上不會發生，但守一下），
        # 沒有 slug 就不推。
        meta = await repo.get_episode_meta(episode_id)
        if not meta:
            continue
        try:
            await notify_user(
                uid,
                {
                    "title": f"「{meta['title']}」已製作完成",
                    "body": "點開就能聽。",
                    "url": f"/player/{meta['slug']}",
                },
            )
        except Exception as exc:
            logger.warning("交付已完成，但推播失敗（uid=%s）: %s", uid, exc)

    return {}


# ── Node 10: backfill_dict（best-effort）─────────────────


async def backfill_dict_node(state: PodState, config: RunnableConfig) -> dict[str, Any]:
    """補缺字翻譯到 dict_translate queue。失敗不擋 generate。"""
    ctx = _ctx(config)
    queue_obj = ctx.get("queue")

    script: ScriptJSON | None = state.get("script")
    if script is None:
        return {}

    try:
        if queue_obj is not None:
            for v in script.target_vocab:
                await queue_obj.send(
                    "dict_translate",
                    {"word": v.word.casefold()},
                )
        else:
            # 跑測試 / demo 時 queue 沒注入 — 走 in-process 的 backfill_dict 函式。
            from engine.pipeline.post_process import backfill_dict  # noqa: PLC0415

            await backfill_dict(script.target_vocab)
    except Exception as exc:
        logger.warning(
            "backfill_dict 失敗（不擋 generate）episode_id=%s type=%s: %s",
            state.get("episode_id"),
            type(exc).__name__,
            exc,
        )

    return {}


