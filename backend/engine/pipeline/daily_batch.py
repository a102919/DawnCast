"""每日公開 podcast 批次：02:00 cron 觸發後，產 2 部公開集進 generate 佇列。

設計重點：
  - 2 個固定 slot，確定性 idempotency_key。
  - `source='daily_batch'` 由 `upsert_episode_node` 自動推導 `is_free=true`
    （見 engine/pipeline/langgraph_pod/nodes.py:1441）。
  - `user_ids=[]`：不建立 delivery；新用戶下單時由 reuse L1 邏輯拿到這批集。
  - 同日 exactly-once 由 DB function `public.enqueue_daily_podcast_batch` 保證，
    本檔只負責組 message + 呼叫 queue 薄殼，不直接碰 pgmq.send。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from shared.db import queue

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _DailySlot:
    big_topic: str
    canonical_topic: str
    angle: str


# B1：2 個固定 slot。
# 確定性 → idempotency_key 穩定 → 重跑測試不浪費 LLM quota；
# 之後可改成依 deliver_date 從 curated catalog deterministic rotate。
_DAILY_SLOTS: tuple[_DailySlot, ...] = (
    _DailySlot("tech", "AI agents at work", "定義"),
    _DailySlot("business", "Compound interest in everyday decisions", "應用場景"),
)


def _build_message(slot: _DailySlot, deliver_date: str) -> dict[str, Any]:
    """把一個 daily slot 轉成完整 generate contract。"""
    return {
        "big_topic": slot.big_topic,
        "canonical_topic": slot.canonical_topic,
        "angle": slot.angle,
        "topic_type": "evergreen",
        "length_tier": "medium",
        "cefr": "B1",
        # 公開集來源（不是用戶下單）；upsert_episode_node 用 source != "specified" 推 is_free=true
        "source": "daily_batch",
        "deliver_date": deliver_date,
        "user_ids": [],  # 不建立 delivery，留給 reuse L1 邏輯接手
        "cluster_id": None,
        "avoid_facts": [],
    }


def build_daily_messages(deliver_date: str) -> list[dict[str, Any]]:
    """建立當日 2 筆 generate message。固定數量 2（DB function 也會檢查）。"""
    messages = [_build_message(slot, deliver_date) for slot in _DAILY_SLOTS]
    if len(messages) != 2:
        raise RuntimeError(
            f"daily batch contract 必須固定 2 筆，實際為 {len(messages)}"
        )
    return messages


async def enqueue_daily_batch(deliver_date: str) -> int:
    """原子 enqueue 當日 2 筆公開 podcast。

    回傳 2 = 首次成功 claim 並送出 2 筆；0 = 該日期已 claim（duplicate control）。
    其他值 = DB function drift，視為失敗。

    send_daily_batch() 把 marker INSERT + 2 send 全收進 SQL function 的單一
    transaction；不應在此改成逐筆 queue.send()，會破壞 exactly-once 語意。
    """
    messages = build_daily_messages(deliver_date)
    enqueued = await queue.send_daily_batch(deliver_date, messages)

    if enqueued == 0:
        logger.info(
            "daily_podcast 已完成或正在由其他 worker 處理，略過 date=%s",
            deliver_date,
        )
    elif enqueued == 2:
        logger.info(
            "daily_podcast enqueue 完成 date=%s count=%d",
            deliver_date,
            enqueued,
        )
    else:
        # 正常 DB function 不應回傳 1~4；保留 log 方便偵測 migration/function 漂移。
        logger.error(
            "daily_podcast enqueue 回傳非預期數量 date=%s count=%d",
            deliver_date,
            enqueued,
        )

    return enqueued
