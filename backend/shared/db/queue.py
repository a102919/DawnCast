"""pgmq 薄包裝：用 psycopg 參數化呼叫 pgmq 函式，不在 DB 內打外部 I/O。

pgmq.read 內建 SKIP LOCKED + visibility timeout（vt）+ read_ct，
所以多個 worker 同時 read 同一條佇列不會搶到同一筆訊息——冪等與重投的基礎。

設計刻意極薄：只暴露 send / read / delete / archive 四個動作 + Msg dataclass。
重試與 dead-letter 策略不在這裡，留給 worker 依 read_ct 判斷（資料結構優先）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from psycopg.rows import dict_row

from shared.db.pool import connection


@dataclass(frozen=True)
class Msg:
    """從佇列讀出的一筆訊息。read_ct 是被讀取次數，dead-letter 判斷的依據。

    enqueued_at 是 pgmq 記錄的入列時間，用來算 queue_wait_ms（見 metrics.py）；
    default None 是為了不破壞既有測試裡直接構造 Msg(...) 的呼叫點。
    """

    msg_id: int
    read_ct: int
    body: dict[str, Any]
    enqueued_at: datetime | None = None


def _as_dict(raw: Any) -> dict[str, Any]:
    """pgmq message 欄位可能是 dict（jsonb 已解碼）或 JSON 字串，皆容錯。"""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed
    return {}


async def send(queue: str, body: dict[str, Any]) -> int:
    """送一筆訊息進佇列，回傳 pgmq 配發的 msg_id。body 以 jsonb 參數化傳入。"""
    async with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        # pgmq.send(queue_name text, msg jsonb) -> bigint
        await cur.execute(
            "select pgmq.send(%s, %s::jsonb) as msg_id",
            (queue, json.dumps(body)),
        )
        row = await cur.fetchone()
    if row is None:  # 理論上 pgmq.send 必回一列
        raise RuntimeError("pgmq.send 未回傳 msg_id")
    return int(row["msg_id"])


async def read(queue: str, vt: int) -> Msg | None:
    """讀一筆訊息並上 vt 秒隱形鎖（SKIP LOCKED）；無訊息回 None。

    vt（visibility timeout）內這筆對其他 read 隱形；逾時未 delete 會自動重投。
    """
    async with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        # pgmq.read(queue_name text, vt int, qty int) -> setof pgmq.message_record
        await cur.execute(
            "select msg_id, read_ct, enqueued_at, message from pgmq.read(%s, %s, 1)",
            (queue, vt),
        )
        row = await cur.fetchone()
    if row is None:
        return None
    return Msg(
        msg_id=int(row["msg_id"]),
        read_ct=int(row["read_ct"]),
        body=_as_dict(row["message"]),
        enqueued_at=row.get("enqueued_at"),
    )


async def read_batch(queue: str, vt: int, qty: int) -> list[Msg]:
    """一次讀 qty 筆（最多）並上 vt 秒隱形鎖；無訊息回空 list。

    給 batch consumer 用：一次拿一批、LLM 一次翻譯、逐筆 delete。
    所有拿到的 msg 共用同一個 vt 鎖；vt 內任一筆未 delete 會自動重投。
    """
    async with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "select msg_id, read_ct, enqueued_at, message from pgmq.read(%s, %s, %s)",
            (queue, vt, qty),
        )
        rows = await cur.fetchall()
    return [
        Msg(
            msg_id=int(r["msg_id"]),
            read_ct=int(r["read_ct"]),
            body=_as_dict(r["message"]),
            enqueued_at=r.get("enqueued_at"),
        )
        for r in rows
    ]


async def delete(queue: str, msg_id: int) -> bool:
    """處理成功後刪除訊息（不再重投）。回傳是否確實刪到。"""
    async with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        # pgmq.delete(queue_name text, msg_id bigint) -> boolean
        await cur.execute("select pgmq.delete(%s, %s) as ok", (queue, msg_id))
        row = await cur.fetchone()
    return bool(row and row["ok"])


async def archive(queue: str, msg_id: int) -> bool:
    """超過 dead-letter 上限的毒訊息搬進封存表，停止重投但保留可稽核。"""
    async with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        # pgmq.archive(queue_name text, msg_id bigint) -> boolean
        await cur.execute("select pgmq.archive(%s, %s) as ok", (queue, msg_id))
        row = await cur.fetchone()
    return bool(row and row["ok"])


async def send_daily_batch(deliver_date: str, bodies: list[dict[str, Any]]) -> int:
    """原子送當日頻道候選批次（0~10 筆）給 generate 佇列。

    包成單一 SQL function 呼叫 `public.enqueue_daily_podcast_batch(date, jsonb)`：
      - 同 deliver_date 第二次呼叫 → function 回 -1（marker ON CONFLICT，未送出
        任何訊息，語意跟「送出 0 筆」不同，呼叫端要分開判讀）
      - 任一 pgmq.send 失敗 → 整 transaction rollback（marker 也撤回）

    bodies 允許空 list（今天沒有合格候選）——仍必須呼叫 SQL function 寫入
    marker，紀錄「今天評估過」，不可在 Python 端短路提前 return，否則下次
    duplicate control 訊息無法判斷今天到底跑過沒有。

    回傳值原封不動往上傳：-1（已 claim）或實際送出筆數（含 0），語意由呼叫端
    （engine/pipeline/daily_batch.py:enqueue_daily_batch）判讀。

    ponytail: 故意不迴圈 `send("generate", body)`，因為那樣無法保證 marker 與 N 筆
    send 的原子性。整個 exactly-once 語意下沉到 SQL function。
    """
    if not 0 <= len(bodies) <= 10:
        raise ValueError(f"daily batch 筆數必須介於 0~10 之間，實際為 {len(bodies)}")

    async with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "select public.enqueue_daily_podcast_batch(%s::date, %s::jsonb) as sent_count",
            (deliver_date, json.dumps(bodies)),
        )
        row = await cur.fetchone()
    if row is None:
        raise RuntimeError("enqueue_daily_podcast_batch 未回傳 sent_count")
    return int(row["sent_count"])
