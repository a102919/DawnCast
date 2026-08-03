"""現在線上全部 episode 用相同主題重新產生，新集確認生成完成後才清掉舊音檔+舊列。

一次性批次腳本，三個子命令，靠本地 JSON state 檔串接狀態（見
plan/sprightly-popping-firefly.md）。prod 執行走 api-ovate container：

    uv run python -m scripts.regenerate_and_purge_episodes snapshot --out /tmp/regen.json
    uv run python -m scripts.regenerate_and_purge_episodes enqueue --state /tmp/regen.json --execute
    uv run python -m scripts.regenerate_and_purge_episodes reconcile --state /tmp/regen.json --execute

reconcile 可重複執行、冪等；只有在同 channel_id/topic/angle 且音檔已產出的「新」
episode（id 不同於舊列）出現後才會刪舊列（R2 物件 + DB row），不會有服務空窗。
不加 --execute 一律 dry-run，只印訊息不動 prod 資料。

episodes 表沒存 topic_type（只存轉譯後的 freshness_class），_topic_type_for 是
有損還原：freshness_class="timely" 猜回 "news"，其餘一律 "evergreen"。主題文字
本身（big_topic/angle）不受影響，但新集的 format（dialogue/monologue，由
topic_type+length_tier 內部推導）可能跟舊集不同。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from psycopg.rows import dict_row

from shared.db import queue
from shared.db.pool import close_pool, connection
from shared.storage import r2

logger = logging.getLogger(__name__)

_STUCK_AFTER_MIN = 20

_SELECT_SNAPSHOT_SQL = """
    select id, channel_id, big_topic, angle, length_tier, cefr_level,
           freshness_class, audio_r2_key, mp4_r2_key, srt_r2_key, audio_r2_keys
    from public.episodes
    order by id
"""

_MATCH_NEW_SQL = """
    select id
    from public.episodes
    where id != all(%(exclude_ids)s::uuid[])
      and channel_id is not distinct from %(channel_id)s
      and big_topic = %(big_topic)s
      and angle = %(angle)s
      and (audio_r2_key is not null or audio_r2_keys != '[]'::jsonb)
    order by created_at desc
    limit 1
"""


def _topic_type_for(freshness_class: str | None) -> str:
    return "news" if freshness_class == "timely" else "evergreen"


def _load_state(state_path: Path) -> list[dict[str, Any]]:
    return json.loads(state_path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def _save_state(state_path: Path, items: list[dict[str, Any]]) -> None:
    state_path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


async def _cmd_snapshot(out_path: Path) -> None:
    async with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(_SELECT_SNAPSHOT_SQL)
        rows = await cur.fetchall()
    items = [
        {
            "old_id": str(r["id"]),
            "channel_id": str(r["channel_id"]) if r["channel_id"] else None,
            "big_topic": r["big_topic"],
            "angle": r["angle"],
            "length_tier": r["length_tier"],
            "cefr_level": r["cefr_level"],
            "topic_type": _topic_type_for(r["freshness_class"]),
            "audio_r2_key": r["audio_r2_key"],
            "mp4_r2_key": r["mp4_r2_key"],
            "srt_r2_key": r["srt_r2_key"],
            "audio_r2_keys": list(r["audio_r2_keys"] or []),
            "status": "captured",
            "msg_id": None,
            "enqueued_at": None,
            "new_id": None,
        }
        for r in rows
    ]
    out_path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("snapshot 完成：%d 筆 → %s", len(items), out_path)


async def _cmd_enqueue(state_path: Path, execute: bool) -> None:
    items = _load_state(state_path)
    today = date.today().isoformat()
    n = 0
    try:
        for item in items:
            if item["status"] != "captured":
                continue
            queue_body: dict[str, Any] = {
                "big_topic": item["big_topic"],
                "angle": item["angle"],
                "cluster_id": None,
                "deliver_date": today,
                "user_ids": [],
                "length_tier": item["length_tier"],
                "cefr": item["cefr_level"],
                "source": "fallback",
                "topic_type": item["topic_type"],
            }
            if item["channel_id"]:
                queue_body["channel_id"] = item["channel_id"]
            if not execute:
                logger.info("[dry-run] 會 enqueue old_id=%s topic=%s/%s", item["old_id"], item["big_topic"], item["angle"])
                continue
            try:
                msg_id = await queue.send("generate", queue_body)
            except Exception:
                # 保留 status="captured"：重跑會自然重試，不會漏；但絕不能往下走把
                # 這筆標成 pending，否則重跑時會被當成「已送出」略過，卻其實沒送到。
                logger.exception("enqueue 失敗 old_id=%s，跳過（重跑會重試）", item["old_id"])
                continue
            item["msg_id"] = msg_id
            item["enqueued_at"] = datetime.now(timezone.utc).isoformat()
            item["status"] = "pending"
            n += 1
            logger.info("enqueued old_id=%s msg_id=%s topic=%s/%s", item["old_id"], msg_id, item["big_topic"], item["angle"])
    finally:
        # finally 確保就算中途出非預期例外，已成功的項目也不會因為沒存檔而在
        # 重跑時被重複送進 generate queue（跟 prod 共用同一份 MiniMax 額度，重複
        # 生成是真的燒錢，不是單純瑕疵）。
        if execute:
            _save_state(state_path, items)
    logger.info("enqueue 完成：%d 筆已送出", n)


def _delete_old_r2_objects(item: dict[str, Any]) -> None:
    for key in (item["audio_r2_key"], item["mp4_r2_key"], item["srt_r2_key"]):
        if key:
            r2.delete_object(key)
    for key in item["audio_r2_keys"] or []:
        r2.delete_object(key)


async def _reconcile_one(
    item: dict[str, Any], exclude_ids: list[str], execute: bool, now: datetime
) -> None:
    async with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            _MATCH_NEW_SQL,
            {
                "exclude_ids": exclude_ids,
                "channel_id": item["channel_id"],
                "big_topic": item["big_topic"],
                "angle": item["angle"],
            },
        )
        match = await cur.fetchone()
        if match is None:
            # 保持 "pending"（不永久改狀態）：worker 是單一序列迴圈處理 61 筆，
            # 排隊晚的項目很正常會超過 _STUCK_AFTER_MIN，但還是要留在下一輪
            # reconcile 的處理範圍內，不能被永久排除——見 stuck 名單只是提示。
            return
        if execute:
            await cur.execute("delete from public.episodes where id = %s", (item["old_id"],))
    # DB delete 要等這個 connection() 區塊結束才真正 commit；R2 物件刪除不可逆，
    # 一定要等 DB row 確認刪成功之後才動，避免 DB delete 因連線問題 rollback，
    # 卻已經刪掉 R2 物件，留下指向不存在物件的 dangling row。
    if execute:
        _delete_old_r2_objects(item)  # 冪等，重跑安全
    item["status"] = "done"
    item["new_id"] = str(match["id"])


async def _cmd_reconcile(state_path: Path, execute: bool) -> None:
    items = _load_state(state_path)
    now = datetime.now(timezone.utc)
    # 排除「這整批快照裡的所有舊集」而不只是自己，避免同主題重複的舊集彼此
    # 互相誤判成對方的「新集」（曾經真實發生：17 筆舊集被錯誤刪除，見事故記錄）。
    exclude_ids = [i["old_id"] for i in items]
    for item in items:
        if item["status"] != "pending":
            continue
        try:
            await _reconcile_one(item, exclude_ids, execute, now)
        except Exception:
            logger.exception("reconcile 失敗 old_id=%s，下輪重試", item["old_id"])
    if execute:
        _save_state(state_path, items)
    counts: dict[str, int] = {}
    for item in items:
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    logger.info("reconcile 統計：%s", counts)
    slow = [
        i["old_id"]
        for i in items
        if i["status"] == "pending"
        and (now - datetime.fromisoformat(i["enqueued_at"])).total_seconds() / 60 > _STUCK_AFTER_MIN
    ]
    if slow:
        logger.warning(
            "還在等的舊集（超過 %d 分鐘沒等到新集，純提示不影響下一輪重跑）：%s",
            _STUCK_AFTER_MIN,
            slow,
        )


def _amain() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp_snap = sub.add_parser("snapshot", help="唯讀抓全部 episode 存成 state JSON")
    sp_snap.add_argument("--out", type=Path, required=True)

    sp_enq = sub.add_parser("enqueue", help="逐筆送 generate queue")
    sp_enq.add_argument("--state", type=Path, required=True)
    sp_enq.add_argument("--execute", action="store_true", help="不加就是 dry-run，只印訊息")

    sp_rec = sub.add_parser("reconcile", help="核對新集是否生成完成，完成就刪舊集音檔+DB row")
    sp_rec.add_argument("--state", type=Path, required=True)
    sp_rec.add_argument("--execute", action="store_true", help="不加就是 dry-run，只印訊息")

    args = p.parse_args()

    async def runner() -> None:
        try:
            if args.cmd == "snapshot":
                await _cmd_snapshot(args.out)
            elif args.cmd == "enqueue":
                await _cmd_enqueue(args.state, args.execute)
            elif args.cmd == "reconcile":
                await _cmd_reconcile(args.state, args.execute)
        finally:
            await close_pool()

    asyncio.run(runner())


if __name__ == "__main__":
    _amain()
