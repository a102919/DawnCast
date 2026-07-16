"""單字本 router：CRUD + search + SM-2 更新 + clear。

對映前端 Api：addVocab/removeVocab/listVocab/searchVocab/updateVocab/clearVocab。
所有查詢以 user_id 收斂（授權在 server）。SQL 全參數化。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from psycopg.rows import dict_row

from app.deps import get_current_user
from app.response import ApiResponse, ok
from app.schemas import AddVocabBody, UpdateVocabBody
from shared.db.pool import connection
from shared.errors import NotFoundError
from shared.models import VocabItem

router = APIRouter(prefix="/vocab", tags=["vocab"])

# 一律連 episodes 把 source_episode_id(uuid) 投影成對外 slug；
# 再連 dict_cache 把字典例句帶出（user_vocab 不存例句，每次讀取時 JOIN，避免資料搬遷）。
_SELECT = """
  select v.id::text as id, v.word, v.lemma, v.pos, v.translation, v.ipa,
         coalesce(e.slug, '') as source_episode_id,
         coalesce(v.source_line_no, 0) as source_line_no,
         coalesce(v.source_timestamp, 0)::float as source_timestamp,
         to_char(v.created_at, 'YYYY-MM-DD"T"HH24:MI:SS"Z"') as created_at,
         v.sense_idx, v.source_sentence, v.source_sentence_zh,
         to_char(v.next_review, 'YYYY-MM-DD') as next_review,
         v.interval_days as interval, v.ease,
         d.example_en, d.example_zh
  from public.user_vocab v
  left join public.episodes e on e.id = v.source_episode_id
  left join public.dict_cache d on d.word = v.lemma
"""


def _row_to_item(row: dict[str, Any]) -> VocabItem:
    return VocabItem.model_validate(row)


@router.get("", response_model=ApiResponse[list[VocabItem]])
async def list_vocab(user_id: str = Depends(get_current_user)) -> ApiResponse[list[VocabItem]]:
    async with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            _SELECT + " where v.user_id = %s order by v.created_at desc",
            (user_id,),
        )
        rows = await cur.fetchall()
    return ok([_row_to_item(r) for r in rows])


@router.get("/search", response_model=ApiResponse[list[VocabItem]])
async def search_vocab(
    query: str = Query(default="", max_length=100),
    user_id: str = Depends(get_current_user),
) -> ApiResponse[list[VocabItem]]:
    # 跳脫 ILIKE 萬用字元（% _ \），讓使用者輸入當字面比對，不被當 pattern。
    escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    pattern = f"%{escaped}%"
    async with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            _SELECT
            + """ where v.user_id = %s
                  and (v.word ilike %s or v.translation ilike %s)
                  order by v.created_at desc""",
            (user_id, pattern, pattern),
        )
        rows = await cur.fetchall()
    return ok([_row_to_item(r) for r in rows])


@router.post("", response_model=ApiResponse[VocabItem])
async def add_vocab(
    body: AddVocabBody, user_id: str = Depends(get_current_user)
) -> ApiResponse[VocabItem]:
    """新增；去重鍵 unique(user_id,lemma,source_episode_id,source_line_no)。

    衝突時回既有列（對齊 mockApi 行為）。slug→uuid 轉換在此。
    """
    async with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        # slug → episode uuid（找不到則存 null，仍可入本）
        await cur.execute(
            "select id from public.episodes where slug = %s", (body.source_episode_id,)
        )
        ep = await cur.fetchone()
        ep_uuid = ep["id"] if ep else None

        await cur.execute(
            """
            insert into public.user_vocab
              (user_id, word, lemma, pos, translation, ipa, sense_idx,
               source_episode_id, source_line_no, source_timestamp, source_sentence,
               source_sentence_zh, next_review, interval_days, ease)
            values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, current_date, 1, 2.5)
            on conflict (user_id, lemma, source_episode_id, source_line_no)
              do nothing
            returning id
            """,
            (
                user_id,
                body.word,
                body.lemma,
                body.pos,
                body.translation,
                body.ipa,
                body.sense_idx,
                ep_uuid,
                body.source_line_no,
                body.source_timestamp,
                body.source_sentence,
                body.source_sentence_zh,
            ),
        )
        inserted = await cur.fetchone()
        if inserted is not None:
            new_id = inserted["id"]
        else:
            # 衝突：撈既有列回傳
            await cur.execute(
                """select id from public.user_vocab
                   where user_id = %s and lemma = %s
                     and source_episode_id is not distinct from %s
                     and source_line_no is not distinct from %s""",
                (user_id, body.lemma, ep_uuid, body.source_line_no),
            )
            existing = await cur.fetchone()
            assert existing is not None  # 衝突必有列
            new_id = existing["id"]

        await cur.execute(_SELECT + " where v.id = %s and v.user_id = %s", (new_id, user_id))
        row = await cur.fetchone()
        await conn.commit()
    assert row is not None
    return ok(_row_to_item(row))


@router.patch("/{vocab_id}", response_model=ApiResponse[None])
async def update_vocab(
    vocab_id: str, body: UpdateVocabBody, user_id: str = Depends(get_current_user)
) -> ApiResponse[None]:
    """更新 SM-2 欄位（nextReview/interval/ease）。只動本人列。"""
    async with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            update public.user_vocab set
              next_review   = coalesce(%s, next_review),
              interval_days = coalesce(%s, interval_days),
              ease          = coalesce(%s, ease),
              updated_at    = now()
            where id::text = %s and user_id = %s
            returning id
            """,
            (body.next_review, body.interval, body.ease, vocab_id, user_id),
        )
        updated = await cur.fetchone()
        await conn.commit()
    if updated is None:
        raise NotFoundError("找不到單字")
    return ok(None)


@router.delete("/{vocab_id}", response_model=ApiResponse[None])
async def remove_vocab(
    vocab_id: str, user_id: str = Depends(get_current_user)
) -> ApiResponse[None]:
    async with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "delete from public.user_vocab where id::text = %s and user_id = %s",
            (vocab_id, user_id),
        )
        await conn.commit()
    return ok(None)


@router.delete("", response_model=ApiResponse[None])
async def clear_vocab(user_id: str = Depends(get_current_user)) -> ApiResponse[None]:
    async with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        await cur.execute("delete from public.user_vocab where user_id = %s", (user_id,))
        await conn.commit()
    return ok(None)
