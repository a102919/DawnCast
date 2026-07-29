"""使用者端公開頻道 router：list / get(slug) / subscribe / unsubscribe / 我的訂閱。

對外一律回 ChannelPublic（不含 theme_prompt 等內部欄位，見 shared/models/api.py 該
model docstring）。訂閱走 user_channel_subscriptions，純關聯表，不影響任何生成排程
（見 migration 0021_channels.sql 註解）。

DB 存取透過 shared/db/channels.py 的既有函式；cover 簽章沿用 admin.py 已在用的
r2.presigned_get_url / presigned_get_urls 底層工具，但另外寫 row→ChannelPublic 的
mapping（不重用 admin.py 的 _channel_from_row：那支回傳 Channel，欄位集不同，兩邊
輸出模型本來就分開，沒有共用的必要）。
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends

from app.deps import get_current_user
from app.response import ApiResponse, ok
from shared.db import channels as channels_db
from shared.errors import NotFoundError
from shared.models import ChannelPublic
from shared.storage import r2

router = APIRouter(prefix="/channels", tags=["channels"])


def _channel_public_from_row(row: dict[str, Any], cover_image_url: str | None) -> ChannelPublic:
    """row + 已簽好的 coverImageUrl → ChannelPublic。多餘欄位（theme_prompt 等）由
    pydantic 的預設 extra="ignore" 行為過濾掉，不必逐欄挑選。
    """
    return ChannelPublic.model_validate({**row, "cover_image_url": cover_image_url})


async def _channel_public_response(row: dict[str, Any]) -> ChannelPublic:
    """單筆場景：簽單一 cover_r2_key（無封面時不必呼叫 R2）。"""
    cover_r2_key = row.get("cover_r2_key")
    cover_image_url = (
        await asyncio.to_thread(r2.presigned_get_url, cover_r2_key) if cover_r2_key else None
    )
    return _channel_public_from_row(row, cover_image_url)


async def _channel_public_list_response(rows: list[dict[str, Any]]) -> list[ChannelPublic]:
    """清單場景：批次簽章所有 cover_r2_key，避免逐筆呼叫各開一次 thread pool round trip。"""
    keys = [r["cover_r2_key"] for r in rows if r.get("cover_r2_key")]
    signed = await asyncio.to_thread(r2.presigned_get_urls, keys) if keys else {}
    return [
        _channel_public_from_row(
            r, signed.get(r["cover_r2_key"]) if r.get("cover_r2_key") else None
        )
        for r in rows
    ]


async def _get_channel_or_404_by_slug(slug: str) -> dict[str, Any]:
    channel = await channels_db.get_channel_by_slug(slug)
    if channel is None:
        raise NotFoundError("找不到頻道")
    return channel


# 注意：/subscriptions 必須註冊在 /{slug} 之前，否則會被 {slug}="subscriptions" 吃掉。


@router.get("", response_model=ApiResponse[list[ChannelPublic]])
async def list_channels_endpoint() -> ApiResponse[list[ChannelPublic]]:
    """全部上架中（status=active）的頻道，供探索頁使用。"""
    rows = await channels_db.list_channels(status="active")
    return ok(await _channel_public_list_response(rows))


@router.get("/subscriptions", response_model=ApiResponse[list[ChannelPublic]])
async def list_my_subscriptions(
    user_id: str = Depends(get_current_user),
) -> ApiResponse[list[ChannelPublic]]:
    """我追蹤的頻道，供首頁「你追蹤的頻道」區塊與推薦集數使用。"""
    rows = await channels_db.list_subscribed_channels(user_id)
    return ok(await _channel_public_list_response(rows))


@router.get("/{slug}", response_model=ApiResponse[ChannelPublic])
async def get_channel_endpoint(slug: str) -> ApiResponse[ChannelPublic]:
    channel = await _get_channel_or_404_by_slug(slug)
    return ok(await _channel_public_response(channel))


@router.post("/{slug}/subscribe", response_model=ApiResponse[None])
async def subscribe_channel(
    slug: str, user_id: str = Depends(get_current_user)
) -> ApiResponse[None]:
    channel = await _get_channel_or_404_by_slug(slug)
    await channels_db.subscribe(user_id, str(channel["id"]))
    return ok(None)


@router.delete("/{slug}/subscribe", response_model=ApiResponse[None])
async def unsubscribe_channel(
    slug: str, user_id: str = Depends(get_current_user)
) -> ApiResponse[None]:
    channel = await _get_channel_or_404_by_slug(slug)
    await channels_db.unsubscribe(user_id, str(channel["id"]))
    return ok(None)
