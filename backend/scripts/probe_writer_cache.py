"""驗證 MiniMax prompt cache 在寫稿 prompt 上到底會不會命中（一次性診斷腳本）。

⚠️ `--execute` 會打真實 API、消耗跟 prod 共用的訂閱額度。但這支腳本刻意把
max_tokens 壓到 32、assistant 回合用罐頭字串（不真的讓模型寫稿），所以整輪
成本遠低於跑一集，跑完會印出額度前後差供對帳。

背景（2026-08-04 實測結論）：MiniMax 的是被動 prefix cache，而且 cache 條目
以「整個曾經送出去的 request」為單位——新請求要命中，必須有一次過去送過的
完整請求剛好是這次請求的前綴。因此：

    [sys, u1] 是 [sys, u1, a1, u2] 的前綴          → 命中
    [sys, u1] 不是 [sys, u2] 的前綴（user 就岔開）  → 不命中，即使 sys 逐字相同

`cache_control: ephemeral` 在 M3 的 anthropic 相容端點是 no-op
（`cache_creation_input_tokens` 恆為 0），走的是自動快取。

本腳本用真實的 `_build_segment_messages` 產 prompt（體積跟 prod 同量級），
比較兩種訊息結構：

    isolated      每段各自 [sys, u_i]                    ← 現行行為
    conversation  [sys,u1] → [sys,u1,a1,u2] → ...        ← 提案

用法：
    uv run python -m scripts.probe_writer_cache            # dry-run，只印形狀與體積
    uv run python -m scripts.probe_writer_cache --execute  # 真的打 API
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from typing import Any

import httpx

from engine.pipeline.langgraph_pod.nodes_writer import _build_segment_messages
from shared.config import get_settings
from shared.models.engine import ScriptLine, SourcedFact, SourceSnippet

logger = logging.getLogger(__name__)

_QUOTA_URL = "https://api.minimax.io/v1/token_plan/remains"
_MAX_TOKENS = 32
# 兩次呼叫之間的間隔。實測 8s 間隔穩定命中；真實 pipeline 每段相隔 60s 以上，
# 這裡取 3s 只是避免把「快取寫入需要時間」誤判成「結構不對」。
_GAP_SEC = 3.0
_SEGMENT_COUNT = 4

# 罐頭 assistant 回合：只為了讓對話長出下一輪的前綴，不需要模型真的寫稿。
# 長度取跟真實單段輸出同量級（~1.2k tokens）才能讓增量成本估得準。
_CANNED_LINE = (
    '{"speaker":"Alex","text":"That is the part everyone gets wrong about it, '
    'and the numbers back it up pretty clearly.","zh":"那正是大家最容易搞錯的地方，'
    '而且數據也相當清楚地支持這一點。","pause_before":false,"emotion":"neutral"}'
)


def _canned_assistant(n_lines: int = 14) -> str:
    return '{"script":[' + ",".join([_CANNED_LINE] * n_lines) + "]}"


def _fake_sources(n: int = 20) -> list[SourceSnippet]:
    """體積對齊 prod：實測每集 15-28 篇 sources、每篇被 _sources_block 截到 800 字元，
    讓 segment system 落在 ~11k tokens。"""
    body = (
        "Researchers tracked the rollout across three continents and found the "
        "adoption curve flattened far earlier than the vendor projections implied. "
        "The report attributes most of the gap to procurement cycles rather than "
        "technical readiness, and notes that the same pattern appeared in the two "
        "previous generations of the technology. "
    ) * 12
    return [
        SourceSnippet(
            id=f"S{i + 1}",
            title=f"Field report {i + 1}: adoption lags projections",
            url=f"https://example.com/report-{i + 1}",
            text=body,
            published_at="2026-07-30",
            source="example.com",
        )
        for i in range(n)
    ]


def _segment_messages(
    idx: int, *, prev_tail: list[ScriptLine], sources: list[SourceSnippet]
) -> list[dict[str, str]]:
    return _build_segment_messages(
        canonical_topic="AI coding assistants in enterprise procurement",
        big_topic="AI 工具導入",
        topic_type="tech",
        angle="為什麼採購流程比技術成熟度更能決定導入速度",
        cefr="B1",
        tone="playful",
        length_tier="standard",
        format="dialogue",
        sources=sources,
        avoid_facts=("上一集已講過的定價變動",),
        segment_index=idx,
        segment_count=_SEGMENT_COUNT,
        segment_focus=f"Segment {idx + 1} focus: procurement friction, concrete example",
        segment_vocab=["procurement", "rollout", "friction"],
        segment_word_target=220,
        is_chapter_boundary=False,
        is_final_segment=(idx == _SEGMENT_COUNT - 1),
        previous_tail_lines=prev_tail,
        extracted_facts=[
            SourcedFact(claim="Adoption flattened earlier than projected", source_ids=["S1"])
        ],
        series_context=("上一集聊了模型能力的躍進",),
    )


async def _quota_percent(client: httpx.AsyncClient, token: str) -> int | None:
    """回傳 5 小時視窗剩餘百分比。remains_time 是視窗剩餘毫秒、不是額度，別用。"""
    try:
        resp = await client.get(_QUOTA_URL, headers={"Authorization": f"Bearer {token}"})
        resp.raise_for_status()
        for entry in resp.json().get("model_remains", []):
            if entry.get("model_name") == "general":
                return int(entry["current_interval_remaining_percent"])
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        logger.warning("查額度失敗：%s", exc)
    return None


async def _call(
    client: httpx.AsyncClient,
    *,
    base_url: str,
    token: str,
    model: str,
    system: str,
    conversation: list[dict[str, str]],
) -> dict[str, int]:
    resp = await client.post(
        f"{base_url.rstrip('/')}/v1/messages",
        headers={
            "Authorization": f"Bearer {token}",
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": model,
            "max_tokens": _MAX_TOKENS,
            "system": system,
            "messages": conversation,
        },
    )
    resp.raise_for_status()
    usage = resp.json().get("usage") or {}
    return {
        "input": int(usage.get("input_tokens") or 0),
        "cache_read": int(usage.get("cache_read_input_tokens") or 0),
        "cache_create": int(usage.get("cache_creation_input_tokens") or 0),
    }


def _shapes(sources: list[SourceSnippet]) -> tuple[str, list[list[dict[str, str]]], list[list[dict[str, str]]]]:
    """回傳 (system, isolated 的每通 conversation, conversation 的每通 conversation)。"""
    tail = [ScriptLine(speaker="Sarah", text="So where does that leave buyers?", zh="那買方到底處在什麼位置？")]
    per_segment = [
        _segment_messages(i, prev_tail=tail if i else [], sources=sources)
        for i in range(_SEGMENT_COUNT)
    ]
    system = per_segment[0][0]["content"]

    isolated = [[{"role": "user", "content": m[1]["content"]}] for m in per_segment]

    conversation: list[list[dict[str, str]]] = []
    acc: list[dict[str, str]] = []
    for msgs in per_segment:
        acc = [*acc, {"role": "user", "content": msgs[1]["content"]}]
        conversation.append(acc)
        acc = [*acc, {"role": "assistant", "content": _canned_assistant()}]
    return system, isolated, conversation


async def _run_shape(
    client: httpx.AsyncClient,
    label: str,
    system: str,
    calls: list[list[dict[str, str]]],
    *,
    base_url: str,
    token: str,
    model: str,
) -> int:
    total_full_price = 0
    for i, conversation in enumerate(calls):
        if i:
            await asyncio.sleep(_GAP_SEC)
        u = await _call(
            client,
            base_url=base_url,
            token=token,
            model=model,
            system=system,
            conversation=conversation,
        )
        sent = u["input"] + u["cache_read"]
        hit = 100.0 * u["cache_read"] / sent if sent else 0.0
        total_full_price += u["input"]
        print(
            f"  {label:12} call{i + 1}  sent={sent:6d}  full_price={u['input']:6d}  "
            f"cache_read={u['cache_read']:6d}  hit={hit:5.1f}%  cache_create={u['cache_create']}"
        )
    print(f"  {label:12} 全額計費 input 合計 = {total_full_price}")
    return total_full_price


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="真的打 API（會消耗額度）")
    args = parser.parse_args()

    settings = get_settings()
    sources = _fake_sources()
    system, isolated, conversation = _shapes(sources)

    print(f"segment system 長度 = {len(system)} chars（prod 同量級 ~11k tokens）")
    print(f"segment 數 = {_SEGMENT_COUNT}，每通 max_tokens = {_MAX_TOKENS}")
    if not args.execute:
        print("\n[dry-run] 每種結構會送出的訊息形狀：")
        for label, calls in (("isolated", isolated), ("conversation", conversation)):
            for i, conv in enumerate(calls):
                roles = ",".join(m["role"][0] for m in conv)
                print(f"  {label:12} call{i + 1}  [sys,{roles}]")
        print("\n加 --execute 才會真的打 API。")
        return

    token = settings.minimax_auth_token
    if not token:
        raise SystemExit("MINIMAX_AUTH_TOKEN 未設定")

    async with httpx.AsyncClient(timeout=120.0) as client:
        before = await _quota_percent(client, token)
        print(f"\n5 小時視窗剩餘 = {before}%\n")
        iso = await _run_shape(
            client,
            "isolated",
            system,
            isolated,
            base_url=settings.minimax_anthropic_base_url,
            token=token,
            model=settings.minimax_model,
        )
        print()
        conv = await _run_shape(
            client,
            "conversation",
            system,
            conversation,
            base_url=settings.minimax_anthropic_base_url,
            token=token,
            model=settings.minimax_model,
        )
        after = await _quota_percent(client, token)

    print()
    if iso:
        print(f"全額計費 input：isolated={iso} → conversation={conv}（{100 * (conv - iso) / iso:+.1f}%）")
    print(f"5 小時視窗剩餘：{before}% → {after}%")
    print(json.dumps({"isolated_full_price": iso, "conversation_full_price": conv}, indent=2))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
