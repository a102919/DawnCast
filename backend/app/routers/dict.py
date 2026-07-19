"""字典 router：GET /dict/lookup?w= 線上 fallback。

主路徑 → dict_cache（後端，灌了 ECDICT + kaikki）。
未命中 → 線上 LLM（MiniMax）翻譯 + upsert 回 dict_cache，再回給前端。
音檔：cache 命中或 LLM 新增後，若 audio_url 為 null，呼叫 dict_audio.synthesize_word_audio
     觸發 TTS 並 best-effort 回寫。任何失敗一律降級（不回 500）。
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
from engine.llm.translate import translate_word
from engine.media.dict_audio import synthesize_word_audio
from shared.db.pool import connection
from shared.lemmatize import lemmatize
from shared.models import DictEntry

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dict", tags=["dict"])


def _row_to_entry(row: dict[str, Any]) -> DictEntry:
    return DictEntry.model_validate(row)


async def _ensure_audio_url(word: str, entry: DictEntry) -> DictEntry:
    """若 entry.audio_url 為空，inline 觸發 TTS 並 best-effort 回寫。

    降級原則：
      - synthesize 拋例外 → log.warning + 回原 entry（永不冒泡 500）
      - synthesize 回 None → 回原 entry（audio_url 維持 None）
      - DB UPDATE 失敗 → log.warning + 回帶 URL 的 entry（本次可播、未來自動重試）
    """
    if entry.audio_url is not None:
        return entry
    try:
        url = await synthesize_word_audio(word)
    except Exception as exc:  # noqa: BLE001 — best-effort
        logger.warning("TTS 合成失敗 word=%s: %s", word, exc)
        return entry
    if url is None:
        return entry
    try:
        async with connection() as conn, conn.cursor() as cur:
            await cur.execute(
                "update public.dict_cache set audio_url = %s where word = %s",
                (url, word),
            )
    except Exception as exc:  # noqa: BLE001 — best-effort
        logger.warning("audio_url 回寫失敗 word=%s: %s", word, exc)
    return entry.model_copy(update={"audio_url": url})


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
        await cur.execute(
            """select word, ipa, pos, translation, exchange, audio_url, example_en, example_zh
               from public.dict_cache
               where word = any(%s::text[])
               order by array_position(%s::text[], word) desc nulls last
               limit 1""",
            (candidates, candidates),
        )
        row = await cur.fetchone()
    if row is not None:
        # row["word"] 是 cache 實際存的 lemma key，用它跑 TTS / 對齊前端 entry.word。
        return ok(await _ensure_audio_url(row["word"], _row_to_entry(row)))

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
        fallback = DictEntry(
            word=word,
            translation=payload["translation"],
            ipa=payload.get("ipa"),
            pos=payload.get("pos") or [],
            example_en=payload.get("example_en"),
            example_zh=payload.get("example_zh"),
        )
        return ok(await _ensure_audio_url(word, fallback))

    if row2 is not None:
        return ok(await _ensure_audio_url(row2["word"], _row_to_entry(row2)))
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
