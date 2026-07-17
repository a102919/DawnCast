"""收藏 router：list（回 slug[]）/ add / remove。

DB user_favorites 存 episode uuid；對外一律用 slug。slug↔uuid 轉換在此。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from psycopg.rows import dict_row

from app.deps import get_current_user
from app.response import ApiResponse, ok
from shared.db.pool import connection
from shared.errors import NotFoundError

router = APIRouter(prefix="/favorites", tags=["favorites"])


@router.get("", response_model=ApiResponse[list[str]])
async def list_favorites(user_id: str = Depends(get_current_user)) -> ApiResponse[list[str]]:
    async with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            select e.slug
            from public.user_favorites f
            join public.episodes e on e.id = f.episode_id
            where f.user_id = %s
            order by f.created_at desc
            """,
            (user_id,),
        )
        rows = await cur.fetchall()
    return ok([r["slug"] for r in rows])


async def _slug_to_uuid(cur: Any, slug: str) -> str:
    await cur.execute("select id from public.episodes where slug = %s", (slug,))
    row = await cur.fetchone()
    if row is None:
        raise NotFoundError("找不到集數")
    return str(row["id"])


@router.post("/{slug}", response_model=ApiResponse[None])
async def add_favorite(slug: str, user_id: str = Depends(get_current_user)) -> ApiResponse[None]:
    async with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        ep_uuid = await _slug_to_uuid(cur, slug)
        await cur.execute(
            """
            insert into public.user_favorites (user_id, episode_id)
            values (%s, %s) on conflict do nothing
            """,
            (user_id, ep_uuid),
        )
        await conn.commit()
    return ok(None)


@router.delete("/{slug}", response_model=ApiResponse[None])
async def remove_favorite(slug: str, user_id: str = Depends(get_current_user)) -> ApiResponse[None]:
    async with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        # 直接以 join 刪除，slug 不存在則 0 列（remove 為冪等）
        await cur.execute(
            """
            delete from public.user_favorites f
            using public.episodes e
            where f.episode_id = e.id and e.slug = %s and f.user_id = %s
            """,
            (slug, user_id),
        )
        await conn.commit()
    return ok(None)
