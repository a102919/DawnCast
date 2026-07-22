"""常駐 worker：消費 pgmq 控制 / 生成 / dict_translate 佇列，跑夜間 pipeline 的所有外部 I/O。

pg_cron 只發控制訊息（0003），LLM/嵌入/ffmpeg 全在這裡跑——DB 內不打外部 HTTP。

冪等 / 重試 / dead-letter（資料結構決定行為，沒有特例分支）：
  - pgmq.read 帶 vt（visibility timeout）：讀出後該訊息隱形 vt 秒。
  - 成功處理 → delete（不再重投）。
  - 失敗：read_ct >= dead_letter_after → archive（毒訊息搬封存）；
          否則「不 delete」→ vt 到期自動重投，下次 read_ct 會 +1。
  - 超時（asyncio.timeout）：同樣不 delete，交給 vt 重投。
這套讓「成功 / 暫時失敗 / 永久失敗」收斂成 delete / 放著 / archive 三條路。

優雅關閉：收到 SIGTERM/SIGINT 設 stop 旗標，跑完手上這筆再退出。
"""

from __future__ import annotations

import asyncio
import logging
import signal
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from engine.pipeline import dict_translate
from engine.pipeline.evergreen import run_evergreen
from engine.pipeline.generate_job import run_generate_job
from engine.pipeline.reuse import resolve_for_user
from shared.config import get_settings
from shared.db import queue, repo
from shared.db.pool import close_pool, open_pool
from shared.db.queue import Msg

logger = logging.getLogger(__name__)

CONTROL_QUEUE = "control"
GENERATE_QUEUE = "generate"
GENERATE_VT = 600  # 生成 job 較重，隱形鎖 10 分鐘（> job_timeout_sec 預設 8 分）
CONTROL_VT = 120
IDLE_SLEEP_SEC = 2.0


class _Shutdown:
    """SIGTERM/SIGINT → 設旗標。主迴圈在邊界檢查，跑完手上這筆才退。"""

    def __init__(self) -> None:
        self.requested = False

    def request(self, *_: object) -> None:
        logger.info("收到關閉訊號，將於目前訊息處理完後優雅退出")
        self.requested = True


# ── 控制訊息分派（orchestrate / evergreen / collect_open）──────────────


def _anchor_date(body: dict[str, Any]) -> str:
    """日期錨點：優先用 control 訊息帶的台北日曆日（cron 注入），否則退回 app 時區當天。

    不可用容器本機 date.today()（通常 UTC）——會與 user tz 寫入的 order_date 跨午夜對不上。
    """
    msg_date = body.get("date")
    if isinstance(msg_date, str) and msg_date:
        return msg_date
    tz = get_settings().app_timezone
    return datetime.now(ZoneInfo(tz)).date().isoformat()


async def _handle_control(body: dict[str, Any]) -> None:
    """control 佇列：依 task 欄位分派。未知 task 記 warning 後當已處理（delete）。"""
    task = body.get("task")
    anchor = _anchor_date(body)
    if task == "orchestrate":
        await _orchestrate(anchor)
    elif task == "evergreen":
        await run_evergreen(anchor)
    elif task == "collect_open":
        # 22:00 收集窗開啟：把當天 pending 訂單翻 queued（不踩已 played 列）。
        # 冪等，crontab 重跑或補班都不會 double-write。
        n = await repo.mark_orders_status_for_date(
            anchor, from_status="pending", to_status="queued"
        )
        logger.info("collect_open：%d 筆訂單翻 queued（date=%s）", n, anchor)
    else:
        logger.warning("未知 control task=%r，略過", task)


async def _orchestrate(request_date: str) -> None:
    """A~E：投影 daily_orders → topic_requests，再對每個 (user, big_topic) 跑重用。

    MVP 分桶＝big_topic 字面（大方向分桶），不跑向量聚類（V2 再議，git 歷史有 cluster.py 骨架）。
    """
    n = await repo.project_orders_to_requests(request_date)
    logger.info("orchestrate：投影 %d 筆 topic_requests（date=%s）", n, request_date)
    requests = await repo.list_requests_for_date(request_date)
    for r in requests:
        # Phase 4：把投影帶下來的 topic_type / length_tier / cefr 傳進 resolve_for_user，
        # 否則下游 Pod 永遠吃 evergreen/medium/B1 預設，入口選擇形同虛設。
        # angle 不帶——讓 resolve_for_user 依該 user 的交付史輪替角度。
        await resolve_for_user(
            user_id=r["user_id"],
            big_topic=r["big_topic"],
            deliver_date=request_date,
            topic_type=r.get("topic_type"),
            length_tier=r.get("length_tier") or "medium",
            cefr=r.get("cefr") or "B1",
        )


# ── 生成訊息處理 ───────────────────────────────────────────────────


async def _handle_generate(body: dict[str, Any], timeout_sec: int) -> None:
    """generate 佇列：包 asyncio.timeout 跑單集生成。超時往外拋給主迴圈判 dead-letter。"""
    async with asyncio.timeout(timeout_sec):
        await run_generate_job(body)


# ── 單筆訊息的成功 / 失敗收斂 ──────────────────────────────────────


async def _compensate_generate_failure(body: dict[str, Any]) -> None:
    """generate job 失敗 compensation：用 body 算 idempotency_key 砍半完成 row。

    只對 generate 佇列有意義（其他佇列 body 沒有 idempotency 欄位）。
    delete_episode_by_idem 內部已加 audio_r2_key IS NULL 條件，不會誤殺健康 row。
    """
    cluster_id = body.get("cluster_id")
    deliver_date = body.get("deliver_date") or ""
    big_topic = body.get("big_topic") or ""
    angle = body.get("angle") or ""
    length_tier = body.get("length_tier") or "medium"
    topic_type = body.get("topic_type") or "evergreen"
    idem = f"{cluster_id or f'{deliver_date}:{big_topic}:{angle}'}:{length_tier}:{topic_type}"
    try:
        n = await repo.delete_episode_by_idem(idem)
        if n > 0:
            logger.warning(
                "generate 失敗 compensation：刪除 %d 筆半完成 row（idem=%s）",
                n,
                idem,
            )
    except Exception:
        # compensation 失敗不影響主流程的 archive/重投決策，記下來就好。
        logger.exception("compensation delete 失敗 idem=%s", idem)


async def _process(
    queue_name: str,
    msg: Msg,
    handler: Any,
    dead_letter_after: int,
) -> None:
    """跑 handler；成功 delete，失敗依 read_ct 決定 archive 或留給 vt 重投。

    generate 失敗時先做 idempotency-based DELETE compensation，避免 render 階段
    掛掉後留下 audio_r2_key IS NULL 的殭屍 row。
    """
    try:
        await handler(msg.body)
    except Exception:
        if queue_name == GENERATE_QUEUE:
            await _compensate_generate_failure(msg.body)
        # read_ct 是「已被讀取次數」（含這次），達上限即毒訊息 → archive。
        if msg.read_ct >= dead_letter_after:
            logger.exception(
                "%s msg_id=%s 失敗且 read_ct=%d 達上限，archive",
                queue_name,
                msg.msg_id,
                msg.read_ct,
            )
            await queue.archive(queue_name, msg.msg_id)
        else:
            logger.exception(
                "%s msg_id=%s 失敗 read_ct=%d，留待 vt 到期重投",
                queue_name,
                msg.msg_id,
                msg.read_ct,
            )
        return
    await queue.delete(queue_name, msg.msg_id)


# ── 主迴圈 ─────────────────────────────────────────────────────────


async def run_worker(shutdown: _Shutdown | None = None) -> None:
    """常駐主迴圈：control 優先於 generate，再輪 dict_translate；全空就小睡。"""
    settings = get_settings()
    shutdown = shutdown or _Shutdown()
    await open_pool()
    logger.info("worker 啟動，輪詢 control / generate / dict_translate 佇列")

    async def gen_handler(body: dict[str, Any]) -> None:
        await _handle_generate(body, settings.job_timeout_sec)

    try:
        while not shutdown.requested:
            ctrl = await queue.read(CONTROL_QUEUE, CONTROL_VT)
            if ctrl is not None:
                await _process(CONTROL_QUEUE, ctrl, _handle_control, settings.dead_letter_after)
                continue

            gen = await queue.read(GENERATE_QUEUE, GENERATE_VT)
            if gen is not None:
                await _process(GENERATE_QUEUE, gen, gen_handler, settings.dead_letter_after)
                continue

            # 第三優先：dict_translate 補缺字（batch 內自帶 delete/archive 收斂，
            # 例外只記 log——訊息沒 delete 就會由 vt 到期重投，不需額外補償）。
            try:
                if await dict_translate.poll_once():
                    continue
            except Exception:
                logger.exception("dict_translate 輪詢失敗，留待 vt 重投")

            await asyncio.sleep(IDLE_SLEEP_SEC)
    finally:
        await close_pool()
        logger.info("worker 已關閉")


def _run_migrations_on_startup() -> None:
    """啟動時跑 apply_migrations（idempotent）。

    ponytail: 同 app/main.py 的 _run_migrations_on_startup — 從 entrypoint shell
    搬進 main process，因為 Zeabur runtime log driver 不抓 PID 1 stdout。
    worker process 是 main process，logger 一定進 Zeabur log。
    """
    import os

    if os.environ.get("APPLY_MIGRATIONS_ON_BOOT", "1") != "1":
        logger.info("APPLY_MIGRATIONS_ON_BOOT != 1, 略過 migrations")
        return
    try:
        from scripts.apply_migrations import main as _apply_migrations

        # ponytail: 傳空 list 而非 None — worker main() 內 sys.argv 是 uvicorn 不會
        # 出現，但保守起見跟 app/main.py 一致，避免日後改 worker 啟動方式時踩雷。
        rc = _apply_migrations([])
        if rc != 0:
            logger.warning("apply_migrations 結束 return code=%d", rc)
        else:
            logger.info("apply_migrations 完成")
    except Exception:
        logger.exception("啟動時跑 apply_migrations 失敗")


def main() -> None:
    """entrypoint：先跑 migrations（idempotent），再裝訊號處理 + 跑主迴圈。"""
    logging.basicConfig(level=logging.INFO)
    _run_migrations_on_startup()
    shutdown = _Shutdown()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, shutdown.request)
    try:
        loop.run_until_complete(run_worker(shutdown))
    finally:
        loop.close()


if __name__ == "__main__":
    main()
