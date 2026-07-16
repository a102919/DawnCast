"""pgmq 薄包裝：用 psycopg 參數化呼叫 pgmq 函式，不在 DB 內打外部 I/O。

pgmq.read 內建 SKIP LOCKED + visibility timeout（vt）+ read_ct，
所以多個 worker 同時 read 同一條佇列不會搶到同一筆訊息——冪等與重投的基礎。

設計刻意極薄：只暴露 send / read / delete / archive 四個動作 + Msg dataclass。
重試與 dead-letter 策略不在這裡，留給 worker 依 read_ct 判斷（資料結構優先）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from psycopg.rows import dict_row

from shared.db.pool import connection


@dataclass(frozen=True)
class Msg:
    """從佇列讀出的一筆訊息。read_ct 是被讀取次數，dead-letter 判斷的依據。"""

    msg_id: int
    read_ct: int
    body: dict[str, Any]


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
            "select msg_id, read_ct, message from pgmq.read(%s, %s, 1)",
            (queue, vt),
        )
        row = await cur.fetchone()
    if row is None:
        return None
    return Msg(
        msg_id=int(row["msg_id"]),
        read_ct=int(row["read_ct"]),
        body=_as_dict(row["message"]),
    )


async def read_batch(queue: str, vt: int, qty: int) -> list[Msg]:
    """一次讀 qty 筆（最多）並上 vt 秒隱形鎖；無訊息回空 list。

    給 batch consumer 用：一次拿一批、LLM 一次翻譯、逐筆 delete。
    所有拿到的 msg 共用同一個 vt 鎖；vt 內任一筆未 delete 會自動重投。
    """
    async with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "select msg_id, read_ct, message from pgmq.read(%s, %s, %s)",
            (queue, vt, qty),
        )
        rows = await cur.fetchall()
    return [
        Msg(
            msg_id=int(r["msg_id"]),
            read_ct=int(r["read_ct"]),
            body=_as_dict(r["message"]),
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
