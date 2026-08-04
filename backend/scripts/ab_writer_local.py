"""本地端 writer_conversation A/B：用真實 run_pod 跑對照，印每集 judge/cache。

跟 probe_writer_cache.py 的差別：那支用罐頭 assistant response，只驗 cache 命中率；
這支跑真實管線（含 judge、render、upload），但只跑 4 集用來比對品質軸。

⚠️ 會打真實 MiniMax API，額度跟 prod 共用一池。4 集 medium tier writer 預估
~10-15 萬 input tokens、~3-5 萬 output tokens。

用法：
  uv run python -m scripts.ab_writer_local

設計：
- 兩個 evergreen topic × 兩種 mode × ABBA ordering（topic1-iso, topic1-conv,
  topic2-conv, topic2-iso），避免順序效應。
- 每集用獨立 deliver_date 撞不同 idem key，避免被 already_rendered 短路。
- 跑完直接從 episodes 表拉每集的 judge_scores + gen_metrics.llm_calls cache 統計。
- 不寫 order_id、不入 channel，永遠走個人化路徑（這條路徑是 0 行為變動）。
- 用 dev bypass 預設 user (00000000-0000-0000-0000-000000000001)。
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

_BACKEND_DIR = Path(__file__).resolve().parents[1]
_ENV_FILE = _BACKEND_DIR / ".env"
if _ENV_FILE.exists():
    for _line in _ENV_FILE.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _k, _v = _line.split("=", 1)
        os.environ.setdefault(_k.strip(), _v.strip())

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("ab_writer_local")

# 兩個 evergreen skill 型 topic：夠短可控制成本、夠具體能跑出有內容的腳本。
_TOPICS = [
    {
        "big_topic": "What makes a function 'pure'",
        "canonical_topic": "Functional programming: pure functions vs impure side effects",
        "angle": "常見誤解",
        "topic_type": "skill",
    },
    {
        "big_topic": "Why CSS variables beat preprocessor mixins",
        "canonical_topic": "CSS custom properties vs Sass mixins for design tokens",
        "angle": "對比",
        "topic_type": "skill",
    },
]

_USER_ID = "00000000-0000-0000-0000-000000000001"
_BASE_DATE = date.today()


def _make_run_specs() -> list[dict[str, Any]]:
    """ABBA：topic1 iso, topic1 conv, topic2 conv, topic2 iso。

    每集 deliver_date +1 天 → 不同 idem key，避免被冪等鍵短路成同一列。
    """
    def _row(topic: dict[str, Any], mode: bool, offset: int) -> dict[str, Any]:
        return {
            **topic,
            "writer_conversation": mode,
            "deliver_date": (_BASE_DATE + timedelta(days=offset)).isoformat(),
        }

    return [
        _row(_TOPICS[0], False, 0),
        _row(_TOPICS[0], True, 1),
        _row(_TOPICS[1], True, 2),
        _row(_TOPICS[1], False, 3),
    ]


async def _run_one(idx: int, spec: dict[str, Any]) -> str | None:
    from engine.pipeline.langgraph_pod import run_pod

    body = {
        "big_topic": spec["big_topic"],
        "canonical_topic": spec["canonical_topic"],
        "angle": spec["angle"],
        "topic_type": spec["topic_type"],
        "deliver_date": spec["deliver_date"],
        "user_ids": [_USER_ID],
        "length_tier": "medium",
        "cefr": "B1",
        "avoid_facts": [],
        "writer_conversation": spec["writer_conversation"],
    }
    mode_tag = "CONV" if spec["writer_conversation"] else "ISO "
    label = f"#{idx + 1} [{mode_tag}] {spec['big_topic'][:50]}"
    print(f"\n>>> {label}", flush=True)
    print(f"    angle={spec['angle']}  date={spec['deliver_date']}", flush=True)
    try:
        episode_id = await run_pod(body)
    except Exception as exc:
        print(f"    ✗ FAILED: {type(exc).__name__}: {exc}", flush=True)
        return None
    print(f"    ✓ episode_id={episode_id}", flush=True)
    return episode_id


async def _fetch_metrics(episode_id: str) -> dict[str, Any] | None:
    import psycopg
    from psycopg.rows import dict_row

    async with await psycopg.AsyncConnection.connect(
        os.environ["DATABASE_URL"], autocommit=True
    ) as conn, conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            select
                e.id,
                e.big_topic,
                e.research_metrics->'judge_scores' as judge_scores,
                e.gen_metrics->'totals' as totals,
                e.gen_metrics->'llm_calls' as llm_calls,
                (e.research_metrics->>'writer_conversation')::bool as r_mode
            from public.episodes e
            where e.id = %s
            """,
            (episode_id,),
        )
        row = await cur.fetchone()
    return dict(row) if row else None


def _aggregate_writer_metrics(llm_calls: list[dict[str, Any]] | None) -> dict[str, Any]:
    """只看 write_script / write_script_failover 節點的 cache 統計。

    Conversation 模式預期：從 call 2 起 cache_read_tokens 高（命中前綴）。
    """
    if not llm_calls:
        return {"calls": 0}
    writer_nodes = ("write_script", "write_script_failover")
    writer_calls = [c for c in llm_calls if c.get("node") in writer_nodes]
    in_sum = sum(c.get("input_tokens", 0) for c in writer_calls)
    out_sum = sum(c.get("output_tokens", 0) for c in writer_calls)
    cache_read_sum = sum(c.get("cache_read_tokens", 0) or 0 for c in writer_calls)
    cache_create_sum = sum(c.get("cache_creation_tokens", 0) or 0 for c in writer_calls)
    sent_sum = in_sum + cache_read_sum
    hit_pct = 100.0 * cache_read_sum / sent_sum if sent_sum else 0.0
    return {
        "calls": len(writer_calls),
        "input_tokens": in_sum,
        "output_tokens": out_sum,
        "cache_read_tokens": cache_read_sum,
        "cache_creation_tokens": cache_create_sum,
        "hit_pct": round(hit_pct, 1),
    }


async def main() -> None:
    specs = _make_run_specs()
    print("=" * 70)
    print("ABBA ordering（A=isolated, B=conversation）：")
    for i, s in enumerate(specs):
        print(f"  #{i + 1} {'CONV' if s['writer_conversation'] else 'ISO '}  {s['big_topic'][:50]}")
    print("=" * 70)

    episode_ids: list[tuple[dict[str, Any], str | None]] = []
    for i, spec in enumerate(specs):
        ep_id = await _run_one(i, spec)
        episode_ids.append((spec, ep_id))

    print("\n" + "=" * 70)
    print("彙總（從 episodes 表讀回）")
    print("=" * 70)
    rows: list[dict[str, Any]] = []
    for spec, ep_id in episode_ids:
        if ep_id is None:
            continue
        m = await _fetch_metrics(ep_id)
        if m is None:
            continue
        rows.append({"spec": spec, "metrics": m})

    if not rows:
        print("\n沒有可讀的 episode，腳本結束。")
        return

    iso_writer_in: list[int] = []
    conv_writer_in: list[int] = []
    header = (
        f"\n{'topic':<55} {'mode':<5} {'calls':>5} "
        f"{'writer_in':>10} {'cache_hit':>10} {'judge_min':>10}"
    )
    print(header)
    print("-" * 110)
    for r in rows:
        spec = r["spec"]
        m = r["metrics"]
        # judge_scores 只在 research_metrics 內（見 nodes_judge.py collector 寫入路徑）。
        js = (m.get("judge_scores") or {}) or {}
        agg = _aggregate_writer_metrics(m.get("llm_calls"))
        mode = "CONV" if spec["writer_conversation"] else "ISO"
        # 5 軸取最小值當 judge_min（最容易暴露品質衰退）
        axis_vals = [v for v in js.values() if isinstance(v, (int, float))]
        judge_min = min(axis_vals) if axis_vals else None
        print(
            f"{spec['big_topic'][:55]:<55} {mode:<5} {agg['calls']:>5} "
            f"{agg['input_tokens']:>10} {agg['hit_pct']:>9.1f}% {str(judge_min):>10}"
        )
        if mode == "ISO":
            iso_writer_in.append(agg["input_tokens"])
        else:
            conv_writer_in.append(agg["input_tokens"])

    if iso_writer_in and conv_writer_in:
        iso_avg = sum(iso_writer_in) / len(iso_writer_in)
        conv_avg = sum(conv_writer_in) / len(conv_writer_in)
        if iso_avg > 0:
            print(
                f"\nwriter 階段 input 平均：isolated={iso_avg:.0f} → "
                f"conversation={conv_avg:.0f}（{(conv_avg - iso_avg) / iso_avg * 100:+.1f}%）"
            )


if __name__ == "__main__":
    asyncio.run(main())
