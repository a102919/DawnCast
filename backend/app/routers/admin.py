"""Ops / admin router：internal debug 用，查 episode / job / token 用量。

授權機制與一般 API 不同——不是 Supabase JWT，是單一固定 token
（X-Admin-Token header，走環境變數 ADMIN_TOKEN 比對，常數時間比對防 timing attack）。
YAGNI：目前只有單一管理員需求，不建 admin_users 表；之後若真的要多管理員，
屆時再加表也不遲。
"""

from __future__ import annotations

import logging
import secrets
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Header, status
from psycopg.rows import dict_row

from app.response import ApiResponse, ok
from app.schemas import AdminEpsGenerateBody
from shared.config import get_settings
from shared.db import queue
from shared.db.pool import connection
from shared.errors import AuthError
from shared.models import (
    AdminEpisode,
    AdminEpsGenerateResponse,
    AdminJobQueue,
    AdminTokenUsageItem,
    AdminTokenUsageResponse,
)

logger = logging.getLogger(__name__)


def require_admin_token(
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
) -> None:
    """驗 X-Admin-Token。fail-closed：ADMIN_TOKEN 未設定（空字串）時一律拒絕，
    不可因為『環境沒設』就放行。對外只回 generic 401，不洩漏比對細節。
    """
    settings = get_settings()
    expected = settings.admin_token
    if not expected or not x_admin_token or not secrets.compare_digest(x_admin_token, expected):
        raise AuthError("認證失敗")


router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin_token)])


_EPISODES_SQL = """
  select
    slug as id,
    title,
    topic,
    cefr_level,
    is_free,
    is_featured,
    coalesce(episode_no, 0) as episode_no,
    coalesce(to_char(published_at, 'YYYY-MM-DD'), '') as published_at,
    to_char(created_at, 'YYYY-MM-DD"T"HH24:MI:SS"Z"') as created_at,
    freshness_class,
    to_char(expires_at, 'YYYY-MM-DD"T"HH24:MI:SS"Z"') as expires_at,
    (audio_r2_keys <> '[]'::jsonb or audio_r2_key is not null) as has_audio
  from public.episodes
  order by created_at desc
  limit 50
"""

_JOBS_SQL = """
  select queue_name, queue_length, newest_msg_age_sec, oldest_msg_age_sec, total_messages
  from pgmq.metrics_all()
"""

_TOKEN_USAGE_AGGREGATE_SQL = """
  select
    coalesce(sum(input_tokens), 0) as total_input_tokens,
    coalesce(sum(output_tokens), 0) as total_output_tokens,
    count(*) as episode_count
  from public.episodes
"""

_TOKEN_USAGE_ITEMS_SQL = """
  select
    slug, title, input_tokens, output_tokens,
    to_char(created_at, 'YYYY-MM-DD"T"HH24:MI:SS"Z"') as created_at,
    to_char(generation_started_at, 'YYYY-MM-DD"T"HH24:MI:SS"Z"') as generation_started_at,
    to_char(generation_finished_at, 'YYYY-MM-DD"T"HH24:MI:SS"Z"') as generation_finished_at,
    gen_metrics
  from public.episodes
  order by created_at desc
  limit 50
"""


@router.get("/episodes", response_model=ApiResponse[list[AdminEpisode]])
async def list_admin_episodes() -> ApiResponse[list[AdminEpisode]]:
    """Debug 用集數清單，含 hasAudio（audio_r2_key 是否已寫入）判斷生成是否完成。"""
    async with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(_EPISODES_SQL)
        rows = await cur.fetchall()
    return ok([AdminEpisode.model_validate(r) for r in rows])


@router.get("/jobs", response_model=ApiResponse[list[AdminJobQueue]])
async def list_admin_jobs() -> ApiResponse[list[AdminJobQueue]]:
    """所有 pgmq 佇列的度量（metrics_all，不硬寫佇列名，新增佇列免改程式碼）。"""
    async with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(_JOBS_SQL)
        rows = await cur.fetchall()
    return ok([AdminJobQueue.model_validate(r) for r in rows])


@router.get("/token-usage", response_model=ApiResponse[AdminTokenUsageResponse])
async def get_admin_token_usage() -> ApiResponse[AdminTokenUsageResponse]:
    """token 用量總覽：全集數 input/output 加總 + 最近 50 筆明細（含分階段耗時）。"""
    async with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(_TOKEN_USAGE_AGGREGATE_SQL)
        agg = await cur.fetchone()
        await cur.execute(_TOKEN_USAGE_ITEMS_SQL)
        rows = await cur.fetchall()
    items: list[AdminTokenUsageItem] = []
    for r in rows:
        gen_metrics = r.get("gen_metrics")
        stages = gen_metrics.get("stages") if isinstance(gen_metrics, dict) else None
        items.append(AdminTokenUsageItem.model_validate({**r, "stages": stages or []}))
    response = AdminTokenUsageResponse(
        total_input_tokens=agg["total_input_tokens"] if agg else 0,
        total_output_tokens=agg["total_output_tokens"] if agg else 0,
        episode_count=agg["episode_count"] if agg else 0,
        items=items,
    )
    return ok(response)


@router.post(
    "/eps/generate",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=ApiResponse[AdminEpsGenerateResponse],
)
async def generate_admin_episode(
    body: AdminEpsGenerateBody,
) -> ApiResponse[AdminEpsGenerateResponse]:
    """直接排入單集生成；202 僅表示已入列，音檔完成需輪詢 /admin/episodes。

    落庫時 source="fallback" → upsert_episode_node 自動設 is_free=True。
    user_ids 留空 → 沒有 deliveries，但 is_free=True → 任何登入者首頁看得到；
    帶 user_ids → 仍 is_free=True，但額外建立 deliveries 給指定使用者。
    """
    deliver_date = body.deliver_date
    if deliver_date is None:
        tz = ZoneInfo(get_settings().app_timezone)
        deliver_date = datetime.now(tz).date().isoformat()

    cluster_id: str | None = None
    queue_body: dict[str, Any] = {
        "big_topic": body.topic,
        "angle": body.angle,
        "cluster_id": cluster_id,
        "deliver_date": deliver_date,
        "user_ids": list(body.user_ids),
        "length_tier": body.length_tier,
        "cefr": body.cefr,
        "source": "fallback",  # → upsert_episode_node 推導 is_free=True
        "topic_type": body.topic_type,
    }

    # 與 nodes.upsert_episode_node + worker._compensate_generate_failure 同公式
    idem_key = (
        f"{cluster_id or f'{deliver_date}:{body.topic}:{body.angle}'}"
        f":{body.length_tier}:{body.topic_type}"
    )

    try:
        msg_id = await queue.send("generate", queue_body)
    except Exception:
        logger.exception(
            "admin 單集生成 enqueue 失敗（topic=%s, deliver_date=%s）",
            body.topic,
            deliver_date,
        )
        raise  # 走全站 unhandled_handler → 500 internal_error

    return ok(
        AdminEpsGenerateResponse(
            idempotency_key=idem_key,
            msg_id=msg_id,
            status="queued",
        )
    )
