"""重用決策：先做 L3 同主題交付史 guard，沒有歷史才依序重用 L1 公開 → L2 私人，
未命中則排新生成（PRD §4.5）。

L1/L2 都用 repo.find_reusable_episode 的同一條 SQL（用 is_free 切換）；只在 caller
沒有該 big_topic 的任一交付史時才進入重用區，避免「同一 user 同主題拿到兩個版本」。

  L1 公開重用：is_free=true，給「從未拿過該主題」的人。
  L2 私人重用：is_free=false，僅在 caller 從未「自己指定過」該主題時才看，
              否則會把 caller 過去的私人集拿給自己（隱私＋重複）。
  L3 強制生成：caller 對該 big_topic 已有任一交付史 → 跳過 L1/L2 → 走生成尾段。
  L4 都沒有：L1/L2 都 miss → 走生成尾段。

未命中時的生成參數由該 user 同主題的「已交付史」決定：
  * angle：輪替 ANGLES taxonomy 中還沒用過的角度（都用過就按次數取模循環）。
  * avoid_facts：把舊集 extracted_facts 的 claim 餵給寫稿 prompt 避重。
同一次查詢（list_prior_episode_meta）餵兩個用途，不加第二趟 DB。

MVP：分桶單位＝big_topic 字面（大方向分桶）。向量聚類延後 V2（git 歷史有 cluster.py 骨架），
所以這裡傳 cluster_id=None 即可，generate job 仍能跑。
"""

from __future__ import annotations

from typing import Any

from shared.db import queue, repo
from shared.models import ANGLES

GENERATE_QUEUE = "generate"

# avoid_facts 上限：舊集 facts 每集 3-5 條，5 集封頂約 25 條，prompt 塞 12 條夠避重。
_MAX_AVOID_FACTS = 12


def _pick_angle(prior: list[dict[str, Any]]) -> str:
    """選下一個未用過的角度；全用過就按已交付集數取模循環。"""
    used = {p["angle"] for p in prior}
    for angle, _desc in ANGLES:
        if angle not in used:
            return angle
    return ANGLES[len(prior) % len(ANGLES)][0]


def _collect_avoid_facts(prior: list[dict[str, Any]]) -> list[str]:
    """攤平舊集 extracted_facts 的 claim。相容新格式（dict 帶 claim）與舊格式（純字串）。"""
    claims: list[str] = []
    for p in prior:
        for fact in p["extracted_facts"]:
            claim = fact.get("claim") if isinstance(fact, dict) else str(fact)
            if claim:
                claims.append(claim)
    return claims[:_MAX_AVOID_FACTS]


async def resolve_for_user(
    *,
    user_id: str,
    big_topic: str,
    deliver_date: str,
    angle: str | None = None,
    cluster_id: str | None = None,
    topic_type: str | None = None,
    length_tier: str = "medium",
    cefr: str = "B1",
    source: str = "fallback",
) -> str | None:
    """對單一 (user, big_topic) 做重用決策。

    命中既有可重用集 → 直接交付，回傳 episode_id。
    未命中 → enqueue 一筆 generate 訊息（帶 big_topic/angle/cefr/avoid_facts/
            cluster_id/收件人/入口 tier/source），回傳 None（這集稍後由 worker 生成並補交付）。

    angle 不指定（None）時依該 user 同主題交付史自動輪替；顯式指定則照用（測試 / 補生成用）。

    source：topic_requests.source（'specified'/'fallback'），決定新集的 is_free
            （見 nodes.upsert_episode_node）。
    """
    has_prior_delivery = await repo.has_delivered_episode_for_topic(user_id, big_topic)

    episode_id: str | None = None
    if not has_prior_delivery:
        # L1：公開集
        episode_id = await repo.find_reusable_episode(
            big_topic,
            user_id,
            length_tier=length_tier,
            cefr=cefr,
            is_free=True,
        )
        # L2：L1 未命中，且 caller 從未指定過該主題，才看私人集
        if episode_id is None:
            has_specified = await repo.has_specified_topic_request(user_id, big_topic)
            if not has_specified:
                episode_id = await repo.find_reusable_episode(
                    big_topic,
                    user_id,
                    length_tier=length_tier,
                    cefr=cefr,
                    is_free=False,
                )

    if episode_id is not None:
        await repo.insert_delivery(user_id, episode_id, deliver_date)
        return episode_id

    avoid_facts: list[str] = []
    if angle is None:
        prior = await repo.list_prior_episode_meta(user_id, big_topic)
        angle = _pick_angle(prior)
        avoid_facts = _collect_avoid_facts(prior)

    body: dict[str, Any] = {
        "big_topic": big_topic,
        "angle": angle,
        "cluster_id": cluster_id,
        "deliver_date": deliver_date,
        "user_ids": [user_id],
        "length_tier": length_tier,
        "cefr": cefr,
        "avoid_facts": avoid_facts,
        "source": source,
    }
    if topic_type is not None:
        body["topic_type"] = topic_type
    await queue.send(GENERATE_QUEUE, body)
    return None
