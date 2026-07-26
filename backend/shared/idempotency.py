"""集數冪等鍵計算：run_pod（先算一份給 collector 用）、nodes.upsert_episode_node
（權威來源）、worker._compensate_generate_failure（失敗補償刪除半完成 row）
三處共用同一公式，避免三份拷貝各自漂移出不同的 angle 預設值。
"""

from __future__ import annotations


def compute_idempotency_key(
    *,
    cluster_id: str | None,
    deliver_date: str,
    big_topic: str,
    angle: str | None,
    length_tier: str | None,
    topic_type: str | None,
) -> str:
    """`cluster_id`（若有）或 `deliver_date:big_topic:angle` 組 base，再併 length_tier/topic_type。

    同日同 big_topic 但不同入口或長度的請求不能共用同一列，否則後送的會覆蓋先前已渲染的集數。
    """
    resolved_angle = angle or "定義"
    resolved_length_tier = length_tier or "medium"
    resolved_topic_type = topic_type or "evergreen"
    base = cluster_id or f"{deliver_date}:{big_topic}:{resolved_angle}"
    return f"{base}:{resolved_length_tier}:{resolved_topic_type}"
