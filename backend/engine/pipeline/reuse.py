"""重用決策：命中既有集就交付，未命中才排生成（PRD §4.5）。

核心是 repo.find_reusable_episode 的單一 anti-join——「過期集不選、已交付集不選」
全收斂進一條 WHERE，這層只負責「命中 → insert_delivery / 未命中 → enqueue generate」
兩條路。沒有第三種特殊情況。

MVP：分桶單位＝big_topic 字面（大方向分桶）。向量聚類延後 V2（見 cluster.py），
所以這裡傳 cluster_id=None 即可，generate job 仍能跑。
"""

from __future__ import annotations

from typing import Any

from shared.db import queue, repo

GENERATE_QUEUE = "generate"


async def resolve_for_user(
    *,
    user_id: str,
    big_topic: str,
    deliver_date: str,
    angle: str = "定義",
    cluster_id: str | None = None,
    topic_type: str | None = None,
    length_tier: str = "medium",
) -> str | None:
    """對單一 (user, big_topic) 做重用決策。

    命中既有可重用集 → 直接交付，回傳 episode_id。
    未命中 → enqueue 一筆 generate 訊息（帶 big_topic/angle/cluster_id/收件人/入口 tier），
            回傳 None（這集稍後由 worker 生成並補交付）。

    Phase 4：把 topic_type / length_tier 一路帶進 find_reusable_episode 與 generate body，
    否則先前 Phase 1-3 加的入口 tier/format 維度在點餐鏈上會被靜默丟掉。
    topic_type 不帶時不過濾（fallback 兜底路徑仍可用 medium 預設集）。
    """
    episode_id = await repo.find_reusable_episode(
        big_topic, user_id, length_tier=length_tier
    )
    if episode_id is not None:
        await repo.insert_delivery(user_id, episode_id, deliver_date)
        return episode_id

    body: dict[str, Any] = {
        "big_topic": big_topic,
        "angle": angle,
        "cluster_id": cluster_id,
        "deliver_date": deliver_date,
        "user_ids": [user_id],
        "length_tier": length_tier,
    }
    if topic_type is not None:
        body["topic_type"] = topic_type
    await queue.send(GENERATE_QUEUE, body)
    return None
