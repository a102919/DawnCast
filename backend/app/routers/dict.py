"""字典 router：GET /dict/lookup?w= 線上 fallback。

主路徑 → dict_cache（後端，灌了 ECDICT + kaikki）。
未命中 → 線上 LLM（MiniMax）翻譯 + upsert 回 dict_cache，再回給前端。
對映前端 DictEntry | null：翻譯失敗仍回 ok(None)。
"""

from __future__ import annotations

import json
import logging
from typing import Any, cast

from fastapi import APIRouter, Depends, Query
from psycopg.rows import dict_row

from app.deps import get_current_user
from app.response import ApiResponse, ok
from engine.llm.translate import translate_word
from shared.db.pool import connection
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

    # ── 主路徑 ────────────────────────────────────────────
    async with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """select word, ipa, pos, translation, exchange, audio_url, example_en, example_zh
               from public.dict_cache where word = %s""",
            (word,),
        )
        row = await cur.fetchone()
    if row is not None:
        return ok(_row_to_entry(row))

    # ── LLM fallback（MiniMax，與 podcast 生成同帳號）────
    payload = await translate_word(word)
    if payload is None or "translation" not in payload:
        return ok(None)

    # 寫回 dict_cache（缺項補，不覆蓋已有）
    try:
        async with connection() as conn, conn.cursor() as cur:
            await cur.execute(
                """
                insert into public.dict_cache (word, ipa, pos, translation, example_en, example_zh)
                values (%s, %s, %s::jsonb, %s, %s, %s)
                on conflict (word) do nothing
                """,
                (
                    word,
                    payload.get("ipa"),
                    json.dumps(payload.get("pos") or [], ensure_ascii=False),
                    payload["translation"],
                    payload.get("example_en"),
                    payload.get("example_zh"),
                ),
            )
            # 讀回（拿到 audio_url 等其它欄位的潛在值）
            await cur.execute(
                """select word, ipa, pos, translation, exchange, audio_url, example_en, example_zh
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
            )
        )

    if row2 is not None:
        return ok(_row_to_entry(cast("dict[str, Any]", row2)))
    return ok(
        DictEntry(
            word=word,
            translation=payload["translation"],
            ipa=payload.get("ipa"),
            pos=payload.get("pos") or [],
            example_en=payload.get("example_en"),
            example_zh=payload.get("example_zh"),
        )
    )
