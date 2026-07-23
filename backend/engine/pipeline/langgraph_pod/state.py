"""LangGraph Pod 的 state 定義。

PodState 是整條管線在 graph 內流動的單一狀態。每個 node 收到一份 state，
回傳 dict 寫回對應 channel；LangGraph 預設會 merge（list 預設 replace，
標 `Annotated[..., operator.add]` 才會 append）。

頻道分四群：
  1. input：從 pgmq body 帶入
  2. request：tone / format 等生成參數
  3. mid / output：腳本、DB row、媒體成品、R2 keys
  4. control：錯誤旗標 / judge 分數 / 重寫次數
"""

from __future__ import annotations

from typing import Annotated, Any, TypedDict

from engine.media import EpisodeArtifacts
from shared.models import ScriptFormat, ScriptJSON, SourceSnippet


def _append(a: list[str], b: list[str]) -> list[str]:
    """reducer for accumulating lists across nodes。"""
    return [*a, *b]


class PodState(TypedDict, total=False):
    # ── input（pgmq body 解開）────────────────────────────────
    body: dict[str, Any]
    big_topic: str
    canonical_topic: str
    angle: str
    topic_type: str
    source: str  # topic_requests.source（'specified'/'fallback'），決定 is_free
    deliver_date: str
    user_ids: list[str]
    cluster_id: str | None
    length_tier: str  # short / medium / long，缺省時 tone_selector 前補 "medium"
    cefr: str  # A2 / B1 / B2，從 users.cefr_target 一路帶下來；缺省退 settings.cefr_level
    avoid_facts: list[str]  # 同 user 同主題舊集的 facts，寫稿 prompt 避重用

    # ── request contract ─────────────────────────────────────
    tone: str  # curious / playful / contemplative / debate
    format: ScriptFormat  # dialogue / monologue，由 resolve_format 依 topic_type×length_tier 決定

    # ── grounding（retrieve_sources_node 填）──────────────────
    sources: list[SourceSnippet]
    grounded: bool  # sources 非空才 True；空 sources 時 judge 的 groundedness 軸跳過不計分

    # ── LLM 輸出 ─────────────────────────────────────────────
    script: ScriptJSON
    engine_used: str
    judge_scores: dict[str, float]
    judge_feedback: Annotated[list[str], _append]
    # 每次 chat.ainvoke 都 append 一筆 {node, input_tokens, output_tokens}，
    # upsert_episode_node 彙總成一行 log，供成本核算（見 chat.py 的 usage_metadata）。
    token_usage: Annotated[list[dict[str, Any]], _append]

    # ── DB row ───────────────────────────────────────────────
    episode_id: str
    slug: str
    idempotency_key: str
    already_rendered: bool

    # ── 媒體成品 ─────────────────────────────────────────────
    artifacts: EpisodeArtifacts
    audio_key: str | None
    srt_key: str | None

    # ── control / 錯誤 ───────────────────────────────────────
    rate_limited: bool
    storage_failed: bool
    rewrite_iterations: int
    errors: Annotated[list[str], _append]
