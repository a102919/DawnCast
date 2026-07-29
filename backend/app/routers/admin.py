"""Ops / admin router：internal debug 用，查 episode / job / token 用量。

授權機制：兩條路徑擇一即可，見 require_admin。
  1. X-Admin-Token header，走環境變數 ADMIN_TOKEN 比對（常數時間比對防 timing attack）。
  2. 既有 Supabase JWT（Google 登入）的 email claim 對上環境變數 ADMIN_EMAIL——
     用已登入的帳號就能開後台，不用每次手動複製貼上 token。
YAGNI：目前只有單一管理員需求，不建 admin_users 表；之後若真的要多管理員，
屆時再加表也不遲。
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from datetime import datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Header, Request, status
from psycopg.rows import dict_row

from app.deps import decode_jwt_payload, extract_bearer_token
from app.response import ApiResponse, ok
from app.schemas import (
    AdminEpsGenerateBody,
    CreateChannelBody,
    UpdateChannelBody,
    UpdateChannelTopicBody,
)
from shared.config import get_settings
from shared.db import channels as channels_db
from shared.db import queue
from shared.db.pool import connection
from shared.errors import AuthError, NotFoundError, PayloadTooLargeError, ValidationError
from shared.models import (
    AdminEpisodeStats,
    AdminEpisodeStatsResponse,
    AdminEpsGenerateResponse,
    AdminJobQueue,
    CamelModel,
    Channel,
    ChannelTopic,
)
from shared.storage import r2

logger = logging.getLogger(__name__)


def require_admin(
    authorization: str | None = Header(default=None),
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
) -> None:
    """驗 admin 身分：X-Admin-Token 或 Supabase JWT email 白名單，擇一即可。
    fail-closed：兩條路徑都沒設定 / 都不符 → 一律拒絕。對外只回 generic 401，
    不洩漏比對細節（不透露是 token 錯還是 email 不符）。

    細節陷阱：
      - secrets.compare_digest 對含 ≥0x80 byte 的 str 會 TypeError（Starlette 用
        latin-1 解 header，會把這種 byte 原樣帶進來），會冒成未認證 500 兼 log
        traceback，也成為「ADMIN_TOKEN 有沒有設」的 oracle。先 encode 成 bytes
        並 catch TypeError → 統一回 401。
      - JWT email claim 只代表 Supabase 專案簽過此帳號，不代表 Google OAuth
        驗證過。若 Supabase Email provider 開著且 Confirm email 關掉，攻擊者
        可用 admin email 自助註冊拿到合法 JWT。雙保險：要求 email_verified 且
        app_metadata.provider == "google"。
    """
    settings = get_settings()
    if settings.admin_token and x_admin_token:
        try:
            # surrogateescape 把 latin-1 解不出的 byte 留成 code point，避免
            # compare_digest 直接 TypeError；對 admin_token 來自 Settings（純
            # ASCII env var）永遠 encode 成功，這裡只防 x_admin_token 端。
            token_bytes = x_admin_token.encode("utf-8", "surrogateescape")
            expected_bytes = settings.admin_token.encode("utf-8", "surrogateescape")
        except (UnicodeError, TypeError):
            token_bytes = expected_bytes = b""
        if secrets.compare_digest(token_bytes, expected_bytes):
            return
    if settings.admin_email:
        token = extract_bearer_token(authorization)
        if token:
            payload = _decode_for_admin(token)
            if _is_authorized_admin(payload, settings.admin_email):
                return
    raise AuthError("認證失敗")


def _decode_for_admin(token: str) -> dict[str, Any] | None:
    """解 JWT payload；驗證失敗 / 沒 exp 視同沒帶憑證，回 None（不 throw）。

    require_exp=True 免費防禦：python-jose 預設驗 exp 但不要求有 exp claim，
    真實 Supabase token 一律帶 exp，但若攻擊者找到任何能自簽的窗口，缺 exp
    的 token 不該被接受。
    """
    try:
        return decode_jwt_payload(token, require_exp=True)
    except AuthError:
        return None


def _is_authorized_admin(payload: dict[str, Any] | None, admin_email: str) -> bool:
    """payload 是否可開後台：email 命中白名單 + email_verified=True +
    app_metadata.provider == "google"。

    拆出來方便測試單獨驗證（見 tests/test_admin.py）。
    """
    if not payload:
        return False
    if payload.get("email_verified") is not True:
        return False
    if str((payload.get("app_metadata") or {}).get("provider") or "") != "google":
        return False
    email = str(payload.get("email") or "")
    return bool(email) and email.lower() == admin_email.lower()


router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])


_EPISODE_STATS_AGGREGATE_SQL = """
  select
    coalesce(sum(input_tokens), 0) as total_input_tokens,
    coalesce(sum(output_tokens), 0) as total_output_tokens,
    coalesce(sum(play_count), 0) as total_play_count,
    count(*) as episode_count
  from public.episodes
"""

# listener_count／favorite_count 是即時跨表統計（無歷史缺口）；play_count 是
# episodes 自身的累積欄位（只從 migration 0023 部署後起算）。
# ponytail: listened_episode_ids 沒有 GIN index，@> 走 seq scan；現在的
# user 數下無感，慢了再補 index。
_EPISODE_STATS_ITEMS_SQL = """
  select
    e.slug as id,
    e.title,
    e.topic,
    e.cefr_level,
    e.is_free,
    coalesce(e.episode_no, 0) as episode_no,
    coalesce(to_char(e.published_at, 'YYYY-MM-DD'), '') as published_at,
    to_char(e.created_at, 'YYYY-MM-DD"T"HH24:MI:SS"Z"') as created_at,
    c.name as channel_name,
    (e.audio_r2_keys <> '[]'::jsonb or e.audio_r2_key is not null) as has_audio,
    e.play_count,
    e.input_tokens,
    e.output_tokens,
    (e.gen_metrics->>'wall_ms')::int as wall_ms,
    coalesce(e.gen_metrics->'stages', '[]'::jsonb) as stages,
    (
      select count(*) from public.user_activity ua
      where ua.listened_episode_ids @> to_jsonb(e.slug)
    ) as listener_count,
    (
      select count(*) from public.user_favorites uf
      where uf.episode_id = e.id
    ) as favorite_count
  from public.episodes e
  left join public.channels c on c.id = e.channel_id
  order by e.created_at desc
  limit 100
"""

_JOBS_SQL = """
  select queue_name, queue_length, newest_msg_age_sec, oldest_msg_age_sec, total_messages
  from pgmq.metrics_all()
"""


@router.get("/episodes", response_model=ApiResponse[AdminEpisodeStatsResponse])
async def get_admin_episode_stats() -> ApiResponse[AdminEpisodeStatsResponse]:
    """單集數據總覽：全集數彙總 + 最近 100 筆明細（播放／聽完／收藏／token／耗時）。"""
    async with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(_EPISODE_STATS_AGGREGATE_SQL)
        agg = await cur.fetchone()
        await cur.execute(_EPISODE_STATS_ITEMS_SQL)
        rows = await cur.fetchall()
    items = [AdminEpisodeStats.model_validate(r) for r in rows]
    response = AdminEpisodeStatsResponse(
        episode_count=agg["episode_count"] if agg else 0,
        total_input_tokens=agg["total_input_tokens"] if agg else 0,
        total_output_tokens=agg["total_output_tokens"] if agg else 0,
        total_play_count=agg["total_play_count"] if agg else 0,
        items=items,
    )
    return ok(response)


@router.get("/jobs", response_model=ApiResponse[list[AdminJobQueue]])
async def list_admin_jobs() -> ApiResponse[list[AdminJobQueue]]:
    """所有 pgmq 佇列的度量（metrics_all，不硬寫佇列名，新增佇列免改程式碼）。"""
    async with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(_JOBS_SQL)
        rows = await cur.fetchall()
    return ok([AdminJobQueue.model_validate(r) for r in rows])


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


# ── 頻道（Channel）機制：admin CRUD + 選題庫 + 封面上傳 ──────────────────
#
# DB 存取一律透過 shared/db/channels.py 的既有函式；該檔簽名已釘死不再擴充，
# 僅「改寫選題文字」這個既有函式沒覆蓋到的窄需求，沿用本檔既有的直接 SQL
# 風格（比照上面的 _EPISODES_SQL 等），不去動 channels.py 的既定介面。


def _opt_str(value: Any) -> str | None:
    """DB 可能回傳 None／uuid.UUID／date／datetime；非 None 一律轉字串。

    channels repo 的 SELECT 沒有像 _EPISODES_SQL 那樣用 to_char 先格式化成
    字串，這裡在 router 邊界統一轉換（比照 app/routers/account.py:_row_to_account）。
    """
    return str(value) if value is not None else None


def _channel_from_row(row: dict[str, Any], cover_image_url: str | None) -> Channel:
    """row + 已簽好的 coverImageUrl → Channel。簽章交給呼叫端（單筆／批次簽法
    不同），這裡只管欄位轉換（uuid/date → str）。
    """
    return Channel.model_validate(
        {
            **row,
            "id": str(row["id"]),
            "cover_image_url": cover_image_url,
            "last_published_at": _opt_str(row.get("last_published_at")),
        }
    )


async def _channel_response(row: dict[str, Any]) -> Channel:
    """單筆頻道場景（建立／更新／封面上傳後）：簽單一 cover_r2_key。

    presigned_get_url 底層是同步 boto3，asyncio.to_thread 避免阻塞 event loop
    （比照 app/services/episode_assembly.py:127 的簽章寫法）。cover_r2_key 為
    None 時不必呼叫 R2，直接回 None。
    """
    cover_r2_key = row.get("cover_r2_key")
    cover_image_url = (
        await asyncio.to_thread(r2.presigned_get_url, cover_r2_key) if cover_r2_key else None
    )
    return _channel_from_row(row, cover_image_url)


async def _channel_list_response(rows: list[dict[str, Any]]) -> list[Channel]:
    """清單場景：批次簽章所有 cover_r2_key（presigned_get_urls），避免逐筆
    呼叫 presigned_get_url 各開一次 thread pool round trip。
    """
    keys = [r["cover_r2_key"] for r in rows if r.get("cover_r2_key")]
    signed = await asyncio.to_thread(r2.presigned_get_urls, keys) if keys else {}
    return [
        _channel_from_row(r, signed.get(r["cover_r2_key"]) if r.get("cover_r2_key") else None)
        for r in rows
    ]


def _channel_topic_response(row: dict[str, Any]) -> ChannelTopic:
    return ChannelTopic.model_validate(
        {
            **row,
            "id": str(row["id"]),
            "channel_id": str(row["channel_id"]),
            "parent_episode_id": _opt_str(row.get("parent_episode_id")),
            "episode_id": _opt_str(row.get("episode_id")),
            "created_at": str(row["created_at"]),
            "decided_at": _opt_str(row.get("decided_at")),
        }
    )


async def _get_channel_or_404(channel_id: str) -> dict[str, Any]:
    """共用 404 守門：訊息只講業務語意，不洩漏內部路徑或 SQL。"""
    channel = await channels_db.get_channel(channel_id)
    if channel is None:
        raise NotFoundError("頻道不存在")
    return channel


async def _find_channel_topic(channel_id: str, topic_id: str) -> dict[str, Any] | None:
    """topic_id 是否真的屬於 channel_id。重用既有 list_channel_topics 撈整批後
    在 Python 端比對，不為了單筆查詢另外幫 shared/db/channels.py 加一支函式。
    """
    topics = await channels_db.list_channel_topics(channel_id)
    return next((t for t in topics if str(t["id"]) == topic_id), None)


async def _rename_channel_topic(topic_id: str, canonical_topic: str) -> None:
    """改寫選題文字。地基層 update_topic_status 只管狀態轉移，不含這個欄位；
    沿用本檔既有的直接 SQL 風格，不擴充 shared/db/channels.py 的既定簽名。
    """
    async with connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "update public.channel_topics set canonical_topic = %s where id = %s",
            (canonical_topic, topic_id),
        )


# 封面 content-type allowlist（明確排除 svg：可內嵌 script，XSS 風險）+ magic bytes。
_COVER_CONTENT_TYPES: dict[str, str] = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
}
_JPEG_MAGIC = b"\xff\xd8\xff"
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _declared_content_length(request: Request) -> int | None:
    """client 自報的 body 大小；header 缺席或不是數字一律回 None（＝不知道），
    交給實際讀進來的 bytes 長度把關——這個 header 只是提早退掉大檔的捷徑，不是信任來源。
    """
    raw = request.headers.get("content-length")
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _sniff_cover_ext(content_type: str, data: bytes) -> str:
    """驗證宣告的 content-type 落在 allowlist，且與實際 bytes 開頭（magic bytes）
    一致——不信任 client 自報的 header，兩者不符一律視為偽造。
    """
    ext = _COVER_CONTENT_TYPES.get(content_type)
    if ext is None:
        raise ValidationError("不支援的圖片格式，僅接受 JPEG / PNG / WebP")

    if content_type == "image/jpeg":
        matches = data[:3] == _JPEG_MAGIC
    elif content_type == "image/png":
        matches = data[:8] == _PNG_MAGIC
    else:  # image/webp
        matches = data[:4] == b"RIFF" and data[8:12] == b"WEBP"

    if not matches:
        raise ValidationError("圖片內容與宣告的格式不符")
    return ext


@router.get("/channels", response_model=ApiResponse[list[Channel]])
async def list_channels_endpoint(status: str | None = None) -> ApiResponse[list[Channel]]:
    """頻道清單（admin 管理用），可選依 status（active/paused/archived）過濾。"""
    rows = await channels_db.list_channels(status=status)
    return ok(await _channel_list_response(rows))


@router.post("/channels", response_model=ApiResponse[Channel])
async def create_channel_endpoint(body: CreateChannelBody) -> ApiResponse[Channel]:
    """建立新頻道。slug 撞到既有頻道會讓 UniqueViolation 往上炸——YAGNI，
    目前不特別接成 409，交給全站 unhandled_handler 落地成 generic 500
    （對外訊息一律「伺服器發生錯誤」，不洩漏 SQL 細節；需要精準 409 語意時
    再接，見 create_channel docstring）。
    """
    channel_id = await channels_db.create_channel(
        slug=body.slug,
        name=body.name,
        theme_prompt=body.theme_prompt,
        topic=body.topic,
        description=body.description,
        topic_type=body.topic_type,
        length_tier=body.length_tier,
        cefr_level=body.cefr_level,
        target_interval_days=body.target_interval_days,
        status=body.status,
    )
    channel = await channels_db.get_channel(channel_id)
    if channel is None:  # 理論上不會發生：剛 insert 成功、id 是它自己回傳的
        raise RuntimeError("建立頻道後查無資料")
    return ok(await _channel_response(channel))


@router.patch("/channels/{channel_id}", response_model=ApiResponse[Channel])
async def update_channel_endpoint(channel_id: str, body: UpdateChannelBody) -> ApiResponse[Channel]:
    """部分更新頻道欄位；只有明確帶到的欄位才會被改動（exclude_unset）。

    不先查存在性——update_channel 對不存在的 id 是無害 no-op（fields 為空時
    甚至不發查詢），最終用 get_channel 的結果同時判斷「更新完成」與「找不到
    就 404」，省一趟查詢。update_channel 的 SET 目標欄位就是 channel_id 本身，
    不像 topic PATCH 有「topic 是否真的屬於這個 channel」的歸屬疑慮。
    """
    fields = body.model_dump(exclude_unset=True)
    if fields:
        await channels_db.update_channel(channel_id, **fields)

    channel = await channels_db.get_channel(channel_id)
    if channel is None:
        raise NotFoundError("頻道不存在")
    return ok(await _channel_response(channel))


@router.get(
    "/channels/{channel_id}/topics",
    response_model=ApiResponse[list[ChannelTopic]],
)
async def list_channel_topics_endpoint(
    channel_id: str, status: str | None = None
) -> ApiResponse[list[ChannelTopic]]:
    """該頻道的選題庫，可選依 status（candidate/scheduled/published/rejected/stale）過濾。"""
    await _get_channel_or_404(channel_id)
    rows = await channels_db.list_channel_topics(channel_id, status=status)
    return ok([_channel_topic_response(r) for r in rows])


@router.patch(
    "/channels/{channel_id}/topics/{topic_id}",
    response_model=ApiResponse[ChannelTopic],
)
async def update_channel_topic_endpoint(
    channel_id: str, topic_id: str, body: UpdateChannelTopicBody
) -> ApiResponse[ChannelTopic]:
    """管理員事後否決（rejected）或復活（candidate）選題，或修正選題文字。

    先查 topic_id 是否真的屬於 channel_id 再寫入——update_topic_status 只認
    topic_id（不吃 channel_id 做範圍限制），若不先驗證歸屬，URL 帶錯
    channel_id 也會直接改到別頻道的選題，等寫完才發現就太遲了。
    """
    existing = await _find_channel_topic(channel_id, topic_id)
    if existing is None:
        raise NotFoundError("選題不存在")

    if body.status is not None:
        await channels_db.update_topic_status(topic_id, body.status)
    if body.canonical_topic is not None:
        await _rename_channel_topic(topic_id, body.canonical_topic)

    updated = await _find_channel_topic(channel_id, topic_id)
    if updated is None:  # 理論上不會發生：剛查到列，中途被刪除的極端 race
        raise NotFoundError("選題不存在")
    return ok(_channel_topic_response(updated))


class ChannelPlanResponse(CamelModel):
    """手動觸發選題已排入 control 佇列的確認資訊。202 僅表示已入列，實際選題
    由 worker 執行（同 AdminEpsGenerateResponse 的 202 語意）。
    """

    channel_id: str
    msg_id: int
    status: Literal["queued"] = "queued"


@router.post(
    "/channels/{channel_id}/plan",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=ApiResponse[ChannelPlanResponse],
)
async def plan_channel_endpoint(channel_id: str) -> ApiResponse[ChannelPlanResponse]:
    """手動觸發該頻道的選題。API process 不做外部 I/O（不直接呼叫選題 LLM），
    只入 control 佇列，實際選題由 worker 執行（仿 POST /admin/eps/generate）。
    """
    await _get_channel_or_404(channel_id)

    try:
        msg_id = await queue.send("control", {"task": "channel_plan", "channel_id": channel_id})
    except Exception:
        logger.exception("channel_plan enqueue 失敗（channel_id=%s）", channel_id)
        raise  # 走全站 unhandled_handler → 500 internal_error

    return ok(ChannelPlanResponse(channel_id=channel_id, msg_id=msg_id, status="queued"))


@router.post("/channels/{channel_id}/cover", response_model=ApiResponse[Channel])
async def upload_channel_cover(channel_id: str, request: Request) -> ApiResponse[Channel]:
    """封面上傳：不用 multipart（省一個 python-multipart 依賴），前端直接以檔案
    bytes 當 body 送出：

        fetch(url, { method: 'POST', body: file,
                      headers: { 'Content-Type': file.type, 'X-Admin-Token': token } })

    Trust boundary 驗證（全部要過，一項都不能省）：
      1. 頻道必須存在（404）
      2. body 非空（400）
      3. 大小不超過 settings.channel_cover_max_bytes（413）
      4. content-type 落在 allowlist，明確排除 svg（400）
      5. 宣告的 content-type 與實際 magic bytes 一致（400）

    ponytail: 原檔直存不產縮圖，列表小圖也載全尺寸；真的變慢再上 Pillow 產多尺寸。
    """
    await _get_channel_or_404(channel_id)

    max_bytes = get_settings().channel_cover_max_bytes
    declared_size = _declared_content_length(request)
    if declared_size is not None and declared_size > max_bytes:
        raise PayloadTooLargeError("封面圖檔超過大小上限")

    data = await request.body()
    if not data:
        raise ValidationError("封面圖檔內容為空")
    if len(data) > max_bytes:
        raise PayloadTooLargeError("封面圖檔超過大小上限")

    content_type = request.headers.get("content-type", "")
    ext = _sniff_cover_ext(content_type, data)

    r2_key = f"channels/{channel_id}/cover.{ext}"
    await asyncio.to_thread(r2.put_object, r2_key, data, content_type)
    await channels_db.set_channel_cover(channel_id, r2_key)

    channel = await _get_channel_or_404(channel_id)
    return ok(await _channel_response(channel))
