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
from shared.models import (
    ClaimVerification,
    EvidenceCard,
    ResearchQuestion,
    ScriptFormat,
    ScriptJSON,
    SourceSnippet,
    VerifiedClaim,
)


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

    # ── 頻道機制（Channel）─────────────────────────────────────
    channel_id: str | None  # 這集屬於哪個頻道；None＝不屬於任何頻道（既有個人化生成路徑）
    channel_topic_id: str | None  # 選題庫候選 id；生成成功後回填該筆狀態為 published
    series_context: list[str]  # 該頻道最近 2-3 集標題，供寫稿自然呼應建立連續感（非避重複用途）

    # ── request contract ─────────────────────────────────────
    tone: str  # curious / playful / contemplative / debate
    format: ScriptFormat  # dialogue / monologue，由 resolve_format 依 topic_type×length_tier 決定

    # ── grounding（gather_evidence_node 填）──────────────────
    sources: list[SourceSnippet]
    grounded: bool  # sources 非空才 True；空 sources 時 judge 的 groundedness 軸跳過不計分

    # ── 研究（研究節點失敗時均安全降級，不阻斷寫稿）────────────
    research_questions: list[ResearchQuestion]
    evidence_cards: list[EvidenceCard]
    verified_claims: list[VerifiedClaim]
    source_conflicts: list[str]
    claim_verification: ClaimVerification

    # ── LLM 輸出 ─────────────────────────────────────────────
    script: ScriptJSON
    engine_used: str
    judge_scores: dict[str, float]
    judge_feedback: Annotated[list[str], _append]
    # 撞 max_rewrite_iterations 前，歷來 judge 最弱一軸分數最高的那版稿子
    # 與其對應分數（quality_judge_node 每輪都比較更新）；cap 時若最後一輪
    # 反而更差，發布這版而非最後一輪，見 judge_decision 設計討論。
    best_script: ScriptJSON
    best_judge_scores: dict[str, float]
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
    audio_keys: list[str]  # per-line mp3 keys；空 list 表示尚未上傳或全部失敗
    srt_key: str | None

    # ── control / 錯誤 ───────────────────────────────────────
    rate_limited: bool
    storage_failed: bool
    rewrite_iterations: int
    errors: Annotated[list[str], _append]
