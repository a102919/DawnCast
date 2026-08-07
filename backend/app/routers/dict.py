"""字典 router：GET /dict/lookup?w= 線上 fallback。

主路徑 → dict_cache（後端，灌了 ECDICT + kaikki）。
未命中 → 線上 LLM（MiniMax）翻譯 + upsert 回 dict_cache，再回給前端。
音檔：方案 B 後前端改走 player.playSegment 從該行 AudioBuffer 抽樣播，
     dict_audio 函式保留但 HTTP 路徑不再觸發 synthesize（見 engine/media/dict_audio.py）。
對映前端 DictEntry | null：翻譯失敗仍回 ok(None)。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, Query
from psycopg.rows import dict_row

from app.deps import get_current_user
from app.response import ApiResponse, ok

# 刻意的 app→engine 邊界破例：查詞要同步回應（走佇列會壞 UX），
# 故在 HTTP 路徑內直呼 engine 的 LLM 翻譯。全 backend 僅此一處。
from engine.llm.translate import translate_word
from shared.db.pool import connection
from shared.lemmatize import lemmatize
from shared.models import DictEntry

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dict", tags=["dict"])


def _row_to_entry(row: dict[str, Any]) -> DictEntry:
    return DictEntry.model_validate(row)


@router.get("/lookup", response_model=ApiResponse[DictEntry | None])
async def lookup_dict(
    w: str = Query(min_length=1),
    _user_id: str = Depends(get_current_user),
) -> ApiResponse[DictEntry | None]:
    word = w.strip().casefold()
    if not word:
        return ok(None)

    # Lemma 候選：原 word 首位、衍生依序往後。例：「trees」→ ["trees", "tre", "tree"]。
    # SQL 用 ORDER BY array_position DESC：位置最晚（最像 lemma 的）命中壓過原 word。
    candidates = lemmatize(word)

    # ── 主路徑：以 lemma 候選清單查 cache，命中優先取最像 lemma 者 ──
    # （解決「點複數、查完整釋義」：lemma 條目存在時壓過原 word 命中）
    async with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        # audio_url 一律回 null：/media static mount 已移除（方案 B），
        # dict_cache 存的舊 URL 全是死連結，回給前端只會 404；發音由前端 TTS 負責
        await cur.execute(
            """select word, ipa, pos, translation, exchange, null as audio_url,
                      example_en, example_zh, mnemonic, senses, core_sense
               from public.dict_cache
               where word = any(%s::text[])
               order by array_position(%s::text[], word) desc nulls last
               limit 1""",
            (candidates, candidates),
        )
        row = await cur.fetchone()
    if row is not None:
        return ok(_row_to_entry(row))

    # ── LLM fallback（MiniMax，與 podcast 生成同帳號；給原始 word 帶 context）──
    payload = await translate_word(word)
    if payload is None or "translation" not in payload:
        return ok(None)

    # 寫回 dict_cache 用原 word（已存在的髒 key 不會被覆蓋，行為相容於改動前）。
    # 新查詢的 garbage-key 風險由候選 SQL 順序吸收：下次第 i 個變化形命中時，仍會優先回 lemma 條目。
    # 寫回 dict_cache（缺項補，不覆蓋已有）
    try:
        async with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """
                insert into public.dict_cache
                  (word, ipa, pos, translation, example_en, example_zh, mnemonic)
                values (%s, %s, %s::jsonb, %s, %s, %s, %s)
                on conflict (word) do nothing
                """,
                (
                    word,
                    payload.get("ipa"),
                    json.dumps(payload.get("pos") or [], ensure_ascii=False),
                    payload["translation"],
                    payload.get("example_en"),
                    payload.get("example_zh"),
                    payload.get("mnemonic"),
                ),
            )
            # 讀回（拿 exchange 等其它欄位；audio_url 同上一律 null）
            await cur.execute(
                """select word, ipa, pos, translation, exchange, null as audio_url,
                          example_en, example_zh, mnemonic, senses, core_sense
                   from public.dict_cache where word = %s""",
                (word,),
            )
            row2 = await cur.fetchone()
    except Exception as exc:  # noqa: BLE001 — best-effort
        logger.warning("dict_cache 寫入失敗（不擋 fallback）word=%s: %s", word, exc)
        # 即便寫入失敗也把翻譯結果回前端（前端至少看到 zh）
        return ok(
            DictEntry(
                word=word,
                translation=payload["translation"],
                ipa=payload.get("ipa"),
                pos=payload.get("pos") or [],
                example_en=payload.get("example_en"),
                example_zh=payload.get("example_zh"),
                mnemonic=payload.get("mnemonic"),
            )
        )

    if row2 is not None:
        return ok(_row_to_entry(row2))
    return ok(
        DictEntry(
            word=word,
            translation=payload["translation"],
            ipa=payload.get("ipa"),
            pos=payload.get("pos") or [],
            example_en=payload.get("example_en"),
            example_zh=payload.get("example_zh"),
            mnemonic=payload.get("mnemonic"),
        )
    )
