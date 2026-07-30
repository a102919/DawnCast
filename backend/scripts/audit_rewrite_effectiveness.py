"""Audit rewrite 效果：比較 rewrite 觸發 vs 未觸發兩組的最終品質與 token 成本。

從 prod DB 拉近 N 天 episodes（預設 30），按 research_metrics.rewrite_iterations
分成「有重寫」與「無重寫」兩組，比對：
  1. 平均 LLM 呼叫次數
  2. 平均 input / output tokens
  3. 平均 judge_scores 各軸分數
  4. 觸發率（rewrite 觸發比例）

這個 audit 不能直接回答「rewrite 是否改善品質」（因為最終 judge_scores 只存
最後一輪），但能回答「rewrite 觸發的頻率 × 額外成本 × 最終品質分數」是否合理。
若 rewrite 觸發頻繁但兩組最終品質分數差距小 → rewrite 是純屬浪費。

用法：
  cd backend
  uv run python -m scripts.audit_rewrite_effectiveness --days 30
  uv run python -m scripts.audit_rewrite_effectiveness --days 7 --output /tmp/audit.md

Ponytail 註解：純一次性 audit script,不上 production scheduler。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

# 跟其他 scripts/*.py 一致:顯式把 .env 灌進 os.environ,避免從 repo root 跑時
# pydantic-settings 找不到 .env。
_BACKEND_DIR = Path(__file__).resolve().parents[1]
_ENV_FILE = _BACKEND_DIR / ".env"
if _ENV_FILE.exists():
    for _line in _ENV_FILE.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        if "=" not in _line:
            continue
        _k, _v = _line.split("=", 1)
        os.environ.setdefault(_k.strip(), _v.strip())


JUDGE_AXES = ("hook_strength", "informativeness", "pacing", "chemistry", "groundedness")


async def fetch_episodes(conn, days: int) -> list[dict]:
    """拉近 N 天有實際生成 metrics 的 episodes（排除空殼）。"""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select
                e.id,
                e.big_topic,
                e.created_at,
                (e.research_metrics->>'rewrite_iterations')::int as rewrite_iterations,
                e.research_metrics->>'judge_verdict' as judge_verdict,
                e.research_metrics->'judge_scores' as judge_scores,
                e.gen_metrics->'totals' as totals,
                jsonb_array_length(
                    coalesce(e.gen_metrics->'llm_calls', '[]'::jsonb)
                ) as llm_call_count
            from public.episodes e
            where e.generation_finished_at is not null
              and e.generation_finished_at > now() - (%s || ' days')::interval
              and jsonb_array_length(coalesce(e.gen_metrics->'llm_calls', '[]'::jsonb)) > 0
            order by e.created_at desc
            """,
            (days,),
        )
        rows = await cur.fetchall()
    return rows


def summarize_group(rows: list[dict], label: str) -> dict:
    """把一組 episodes 彙整成平均統計。"""
    if not rows:
        return {"label": label, "count": 0}

    in_tokens = [r["totals"].get("input_tokens", 0) for r in rows if r.get("totals")]
    out_tokens = [r["totals"].get("output_tokens", 0) for r in rows if r.get("totals")]
    llm_counts = [r["llm_call_count"] for r in rows]
    axis_scores: dict[str, list[float]] = {axis: [] for axis in JUDGE_AXES}
    for r in rows:
        scores = r.get("judge_scores") or {}
        if isinstance(scores, dict):
            for axis in JUDGE_AXES:
                v = scores.get(axis)
                if isinstance(v, (int, float)):
                    axis_scores[axis].append(float(v))

    def avg(xs: list[float]) -> float:
        return round(statistics.mean(xs), 2) if xs else 0.0

    def stdev(xs: list[float]) -> float:
        return round(statistics.stdev(xs), 3) if len(xs) > 1 else 0.0

    return {
        "label": label,
        "count": len(rows),
        "input_tokens_avg": avg(in_tokens),
        "input_tokens_stdev": stdev(in_tokens),
        "output_tokens_avg": avg(out_tokens),
        "llm_calls_avg": round(statistics.mean(llm_counts), 1),
        "llm_calls_stdev": round(statistics.stdev(llm_counts), 1) if len(llm_counts) > 1 else 0.0,
        "judge_axes_avg": {axis: avg(axis_scores[axis]) for axis in JUDGE_AXES},
        "judge_axes_stdev": {axis: stdev(axis_scores[axis]) for axis in JUDGE_AXES},
    }


def render_markdown(
    no_rewrite: dict,
    with_rewrite: dict,
    total: int,
    days: int,
) -> str:
    lines: list[str] = []
    lines.append(f"# Rewrite 效果審計報告（近 {days} 天）")
    lines.append("")
    lines.append(f"**總集數**:{total}（{no_rewrite['count']} 無重寫 + {with_rewrite['count']} 有重寫）")
    lines.append(
        f"**觸發率**:{round(with_rewrite['count'] / total * 100, 1) if total else 0}%"
    )
    lines.append("")
    lines.append("## 平均成本")
    lines.append("")
    lines.append("| 指標 | 無重寫 | 有重寫 | 差距 |")
    lines.append("|---|---|---|---|")
    for label, key in [
        ("LLM 呼叫次數", "llm_calls_avg"),
        ("Input tokens", "input_tokens_avg"),
        ("Output tokens", "output_tokens_avg"),
    ]:
        a = no_rewrite.get(key, 0)
        b = with_rewrite.get(key, 0)
        diff = round(b - a, 1) if (a and b) else "—"
        diff_pct = f"{round((b - a) / a * 100, 1)}%" if a else "—"
        lines.append(f"| {label} | {a} | {b} | {diff} ({diff_pct}) |")
    lines.append("")
    lines.append("## Judge 五軸分數（最終一輪）")
    lines.append("")
    lines.append("| 軸 | 無重寫 avg | 有重寫 avg | 差距 |")
    lines.append("|---|---|---|---|")
    for axis in JUDGE_AXES:
        a = no_rewrite["judge_axes_avg"].get(axis, 0)
        b = with_rewrite["judge_axes_avg"].get(axis, 0)
        diff = round(b - a, 3)
        lines.append(f"| {axis} | {a} | {b} | {diff:+} |")
    lines.append("")

    # 簡單 verdict
    trigger_rate = with_rewrite["count"] / total if total else 0
    quality_gap = sum(
        with_rewrite["judge_axes_avg"].get(axis, 0) - no_rewrite["judge_axes_avg"].get(axis, 0)
        for axis in JUDGE_AXES
    ) / len(JUDGE_AXES)

    cost_gap_pct = (
        round((with_rewrite["input_tokens_avg"] - no_rewrite["input_tokens_avg"])
              / no_rewrite["input_tokens_avg"] * 100, 1)
        if no_rewrite.get("input_tokens_avg")
        else 0
    )

    lines.append("## 結論")
    lines.append("")
    lines.append(f"- **觸發率**:{round(trigger_rate * 100, 1)}%")
    lines.append(f"- **平均品質差距**(有重寫 − 無重寫,五軸平均):{quality_gap:+.3f}")
    lines.append(f"- **Input token 成本差距**(+%):{cost_gap_pct:+}%")

    if trigger_rate < 0.05:
        verdict = "觸發率極低（< 5%），rewrite 路徑幾乎沒被走到，可考慮保留但不急著優化。"
    elif abs(quality_gap) < 0.03:
        verdict = (
            f"觸發率 {round(trigger_rate * 100, 1)}% 但最終品質差距幾乎為零"
            f"（{quality_gap:+.3f}），rewrite 純屬浪費。**建議**:`max_rewrite_iterations` 預設 1 → 0,"
            f"或保留設定但 P1 跳過 outline 重打（沒重打時也無 outline 可重用）。"
        )
    elif cost_gap_pct > 50 and quality_gap < 0.05:
        verdict = (
            f"成本差距 {cost_gap_pct:+}% 但品質提升僅 {quality_gap:+.3f}，CP 值極低。"
            f"**建議**:進入 P1（跳過 outline 重打）省 ~5% input,"
            f"或考慮把 `max_rewrite_iterations` 預設降為 0。"
        )
    elif quality_gap >= 0.05:
        verdict = (
            f"觸發率 {round(trigger_rate * 100, 1)}%、品質提升 {quality_gap:+.3f},"
            f"rewrite 有實質價值。**建議**:保留 rewrite,進入 P1 把 outline 重打省下來。"
        )
    else:
        verdict = "資料量太少無法下定論,建議拉長 days 再跑一次。"

    lines.append(f"- **判定**:{verdict}")
    lines.append("")
    return "\n".join(lines)


async def main(days: int, output: str | None) -> None:
    # 延遲 import: 確保 os.environ 已灌進 .env
    from shared.db.pool import close_pool, connection  # noqa: PLC0415

    async with connection() as conn:
        rows = await fetch_episodes(conn, days)

    no_rewrite = [r for r in rows if (r.get("rewrite_iterations") or 0) == 0]
    with_rewrite = [r for r in rows if (r.get("rewrite_iterations") or 0) > 0]

    a = summarize_group(no_rewrite, "無重寫")
    b = summarize_group(with_rewrite, "有重寫")
    md = render_markdown(a, b, total=len(rows), days=days)

    if output:
        Path(output).write_text(md, encoding="utf-8")
        print(f"寫到 {output}（{len(rows)} 集,{len(with_rewrite)} 有重寫）")
    else:
        print(md)

    await close_pool()


def build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Audit rewrite 觸發率與成本")
    p.add_argument("--days", type=int, default=30, help="往回看幾天（預設 30）")
    p.add_argument("--output", default=None, help="輸出到檔案（不指定就印 stdout）")
    return p


if __name__ == "__main__":
    args = build_argparser().parse_args()
    asyncio.run(main(args.days, args.output))
