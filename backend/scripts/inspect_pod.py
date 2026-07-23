"""本地手動跑一集 podcast pipeline，逐節點計時找卡點。

跳過 pgmq / pg_cron，直接呼叫 run_pod() 帶 production 形狀 body。
用 graph.astream(stream_mode="events") 抓每個 node 進出的時間戳。

用法：
  cd backend && uv run python -m scripts.inspect_pod --topic "Rust async runtime"
  uv run python -m scripts.inspect_pod --topic "TypeScript narrow" --length short --cefr A2
  uv run python -m scripts.inspect_pod --topic "..." --mock    # 對照組（mock 全部）

輸出格式：
  [00.0] ▶ enter retrieve_sources_node
  [03.2] ✓ retrieve_sources_node   (3.21s)
  [03.2] ▶ enter tone_selector_node
  ...
  [60.4] ✓ backfill_dict_node     (0.05s)    ← 收尾
  [60.4] episode_id=...
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

# pydantic-settings 的 env_file 是相對 CWD — 從 backend/ 之外的 shell 跑會讀不到。
# 顯式把 backend/.env 灌進 os.environ，等同 docker-compose 的 env_file 行為。
_BACKEND_DIR = Path(__file__).resolve().parents[1]
_ENV_FILE = _BACKEND_DIR / ".env"
if _ENV_FILE.exists():
    for _line in _ENV_FILE.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _k, _v = _line.split("=", 1)
        os.environ.setdefault(_k.strip(), _v.strip())

logging.basicConfig(
    level=logging.WARNING,  # 只留 WARNING+，其他全部交給我們自己的印
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("inspect_pod")


def build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="本地手動跑 pod 找卡點")
    p.add_argument("--topic", default="Rust async runtime internals")
    p.add_argument(
        "--angle",
        default="定義",
        choices=["定義", "人物故事", "常見誤解", "應用場景", "歷史", "對比"],
    )
    p.add_argument(
        "--length",
        default="medium",
        choices=["short", "medium", "long"],
    )
    p.add_argument(
        "--cefr",
        default="B1",
        choices=["A1", "A2", "B1", "B2", "C1", "C2"],
    )
    p.add_argument(
        "--user-id",
        default="00000000-0000-0000-0000-000000000001",
        help="收件人 user_id（用 dev bypass 預設 user）",
    )
    p.add_argument(
        "--timeout-sec",
        type=int,
        default=480,
        help="整集硬切時間（等同 prod job_timeout_sec）",
    )
    p.add_argument(
        "--mock",
        action="store_true",
        help="走 in-memory mock（不連 DB / LLM）",
    )
    p.add_argument(
        "--trace-to-db",
        action="store_true",
        help="每個 node 完成時把 trace 寫進 inspect_traces 表（prod trace 用）",
    )
    return p


# ── 印時間戳小工具 ────────────────────────────────────


class _Timer:
    def __init__(self) -> None:
        self.t0 = time.monotonic()

    def stamp(self) -> float:
        return time.monotonic() - self.t0


class _TraceWriter:
    """Lazy psycopg writer：每個 node 完成 INSERT 一行 trace。

    prod 用 — Zeabur execute-command 120s timeout，inspect_pod foreground 跑
    245s+ 看不到 stdout。繞法：trace 寫 DB，跑完從 db-pran psql 拉。
    """

    def __init__(self) -> None:
        self.run_id = os.environ.get(
            "INSPECT_RUN_ID",
            f"inspect-{int(time.time())}-{os.getpid()}",
        )
        self._conn: object | None = None
        self._topic = ""
        self._engine = ""

    async def open(self, *, topic: str, engine: str) -> None:
        import psycopg

        self._topic = topic
        self._engine = engine
        # 連 db-pran（worker 預設 DATABASE_URL 指向 db-pran.zeabur.internal）
        self._conn = await psycopg.AsyncConnection.connect(
            os.environ["DATABASE_URL"], autocommit=True
        )
        # CREATE TABLE IF NOT EXISTS 冪等；topic / engine 補欄方便後續查
        await self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS inspect_traces (
                run_id    text      NOT NULL,
                seq       int       NOT NULL,
                ts        double precision NOT NULL,
                delta     double precision NOT NULL,
                node_name text      NOT NULL,
                topic     text      NOT NULL DEFAULT '',
                engine    text      NOT NULL DEFAULT '',
                errors    jsonb     NOT NULL DEFAULT '[]'::jsonb,
                PRIMARY KEY (run_id, seq)
            )
            """
        )

    async def record(
        self, *, seq: int, ts: float, delta: float, node_name: str, errors: list
    ) -> None:
        if self._conn is None:
            return
        import json as _json

        await self._conn.execute(  # type: ignore[attr-defined]
            "INSERT INTO inspect_traces "
            "(run_id, seq, ts, delta, node_name, topic, engine, errors) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb) "
            "ON CONFLICT (run_id, seq) DO NOTHING",
            (
                self.run_id,
                seq,
                ts,
                delta,
                node_name,
                self._topic,
                self._engine,
                _json.dumps(errors or []),
            ),
        )

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()  # type: ignore[attr-defined]
            self._conn = None


async def _run_with_events(args: argparse.Namespace) -> int:
    from engine.pipeline.langgraph_pod import build_pod

    timer = _Timer()
    body = {
        "big_topic": args.topic,
        "canonical_topic": args.topic,
        "angle": args.angle,
        "topic_type": "skill",  # 'skill' topic_type 是 tech 入口常見
        "deliver_date": date.today().isoformat(),
        "user_ids": [args.user_id],
        "length_tier": args.length,
        "cefr": args.cefr,
        "avoid_facts": [],
    }

    print("=" * 70)
    print(f"[{timer.stamp():6.2f}] inspect_pod")
    print(f"  topic={args.topic!r}")
    print(f"  angle={args.angle}  length_tier={args.length}  cefr={args.cefr}")
    print(f"  user_id={args.user_id}")
    print(f"  timeout={args.timeout_sec}s  mock={args.mock}")
    print("=" * 70)

    chat = None
    chat_failover = None
    repo = None
    r2 = None
    queue = None
    renderer = None
    if args.mock:
        from engine.pipeline.langgraph_pod.chat import FakeChatModel
        from engine.pipeline.langgraph_pod.mock import (
            MockRenderer,
            get_mocks,
            make_mock_workdir,
        )

        chat = FakeChatModel(
            responses=['{"topic":"x","extracted_facts":[],"target_vocab":[],"script":[]}'] * 4,
            judge_responses=[
                '{"hook_strength":0.8,"informativeness":0.8,"pacing":0.8,'
                '"chemistry":0.8,"groundedness":1.0,"feedback":[]}'
            ] * 4,
        )
        chat_failover = None
        repo, r2, queue = get_mocks(reset=True)
        renderer = MockRenderer(make_mock_workdir())
        print(f"[{timer.stamp():6.2f}] mock 模式（不連 DB / 不打 LLM）")
    else:
        # production：讓 run_pod 自己開 pool + 組 chat / repo / r2
        print(f"[{timer.stamp():6.2f}] production 模式（會打 LLM / TTS / 寫 DB）")

    graph = build_pod(checkpointer=None)  # type: ignore[arg-type]

    config: dict[str, object] = {
        "configurable": {
            "thread_id": f"inspect:{date.today().isoformat()}:{args.topic}",
        },
    }
    if args.mock:
        from shared.config import get_settings

        cfg = get_settings()
        config["configurable"] = {  # type: ignore[assignment]
            **config["configurable"],  # type: ignore[operator]
            "chat": chat,
            "chat_failover": chat_failover,
            "repo": repo,
            "r2": r2,
            "queue": queue,
            "renderer": renderer,
            "settings": cfg,
        }
    else:
        from shared.config import get_settings

        cfg = get_settings()
        # 幫 inspect 開 override：failover_mode=degrade（預設）→ 撞限流就 graceful END，
        # 不用切 failover chat。failover_mode 可從 CLI 開但先不加，保持簡單。
        from engine.pipeline.langgraph_pod import _build_runtime_context

        runtime = _build_runtime_context(cfg, use_mock=False, reset_mocks=False)
        config["configurable"] = {  # type: ignore[assignment]
            **config["configurable"],  # type: ignore[operator]
            **runtime,
        }

    # ── stream updates 逐節點計時 ─────────────────────────────
    # stream_mode='updates' 在 node 完成後吐 {node_name: state_delta}，比 events mode 簡潔。
    events: list[tuple[float, str, float]] = []  # (ts, node_name, delta)
    last_ts = timer.stamp()

    def _emit(ts: float, msg: str) -> None:
        print(f"[{ts:6.2f}] {msg}", flush=True)

    _emit(timer.stamp(), "開始 graph.astream(stream_mode='updates')")
    trace = _TraceWriter()
    if args.trace_to_db:
        engine_name = "mock" if args.mock else "prod"
        await trace.open(topic=args.topic, engine=engine_name)
        _emit(
            timer.stamp(),
            f"trace DB mode 啟用：run_id={trace.run_id}",
        )
    seq = 0
    try:
        async with asyncio.timeout(args.timeout_sec):
            async for ev in graph.astream(
                {
                    "body": body,
                    "big_topic": body["big_topic"],
                    "canonical_topic": body["canonical_topic"],
                    "angle": body["angle"],
                    "topic_type": body["topic_type"],
                    "deliver_date": body["deliver_date"],
                    "user_ids": body["user_ids"],
                    "cluster_id": None,
                    "length_tier": body["length_tier"],
                    "cefr": body["cefr"],
                    "avoid_facts": body["avoid_facts"],
                    "rewrite_iterations": 0,
                    "judge_feedback": [],
                    "errors": [],
                    "rate_limited": False,
                    "storage_failed": False,
                    "already_rendered": False,
                },
                config=config,
                stream_mode="updates",
            ):
                # ev 形如 {node_name: state_delta_dict}
                now = timer.stamp()
                for node_name, state_delta in ev.items():
                    if node_name == "__interrupt__":
                        continue
                    delta = now - last_ts
                    last_ts = now
                    events.append((now, node_name, delta))
                    _emit(now, f"✓ {node_name:<35s} (delta={delta:6.2f}s)")
                    if args.trace_to_db:
                        seq += 1
                        node_errors = (
                            list(state_delta.get("errors", []))
                            if isinstance(state_delta, dict)
                            else []
                        )
                        await trace.record(
                            seq=seq,
                            ts=now,
                            delta=delta,
                            node_name=node_name,
                            errors=node_errors,
                        )

                    # 顯示重要的 state 變化
                    if state_delta and isinstance(state_delta, dict):
                        if "script" in state_delta and state_delta["script"]:
                            n_lines = (
                                len(state_delta["script"].get("script", []))
                                if isinstance(state_delta["script"], dict)
                                else "?"
                            )
                            _emit(now, f"   ↳ script lines={n_lines}")
                        if "errors" in state_delta and state_delta["errors"]:
                            _emit(now, f"   ↳ errors: {state_delta['errors']}")
                        if "rate_limited" in state_delta:
                            _emit(now, f"   ↳ rate_limited={state_delta['rate_limited']}")
                        if "episode_id" in state_delta and state_delta["episode_id"]:
                            _emit(now, f"   ↳ episode_id={state_delta['episode_id']}")
                        if "rewrite_iterations" in state_delta:
                            _emit(now, f"   ↳ rewrite_iter={state_delta['rewrite_iterations']}")
                        if "judge_feedback" in state_delta and state_delta["judge_feedback"]:
                            _emit(now, f"   ↳ judge_feedback={state_delta['judge_feedback']}")

    except asyncio.TimeoutError:
        print(f"[{timer.stamp():6.2f}] ⏰ TIMEOUT ({args.timeout_sec}s) 抵達；node_entered={list(node_entered)}")
        await trace.close()
        return 3
    except Exception as exc:
        print(f"[{timer.stamp():6.2f}] ✗ EXCEPTION {type(exc).__name__}: {exc}")
        import traceback
        traceback.print_exc()
        await trace.close()
        return 2

    print()
    print("=" * 70)
    print(f"[{timer.stamp():6.2f}] 完成，總節點數 {len(events)}")
    print("=" * 70)
    await trace.close()

    # 列出「最慢前 5 名」
    if events:
        print("\n最慢 5 個 node（delta = 跟前一個 node 的間隔）：")
        for ts, name, d in sorted(events, key=lambda x: -x[2])[:5]:
            print(f"  {d:6.2f}s  {name}")
    return 0


def main() -> int:
    args = build_argparser().parse_args()
    return asyncio.run(_run_with_events(args))


if __name__ == "__main__":
    raise SystemExit(main())
