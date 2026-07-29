"""每日公開 podcast 批次：01:00 channel_plan 選題後，02:00 從 channel_topics 挑今天
要生成的候選集數，送進 generate 佇列。

設計重點：
  - 頻道機制決定「今天實際要生幾集」（0..channel_daily_max_slots），不是寫死常數；
    候選不足就少產，甚至 0 集——「沒內容就不產」是刻意設計，見
    shared/db/channels.py:pick_daily_topics。
  - `source='daily_batch'` 由 upsert_episode_node 自動推導 is_free=true
    （見 engine/pipeline/langgraph_pod/nodes.py:1441）。
  - `user_ids=[]`：不建立 delivery；新用戶下單時由 reuse L1 邏輯拿到這批集。
  - 同日 exactly-once 由 DB function `public.enqueue_daily_podcast_batch` 保證：
    -1＝該 deliver_date 已被其他 worker claim（重複 control 訊息，這批訊息完全
    沒被送出）；0 或正整數＝正常完成，值即實際送出筆數（0 是合法結果：今天
    評估過，沒有合格候選）。本檔只負責組 message + 呼叫 queue 薄殼，不直接碰
    pgmq.send。
"""

from __future__ import annotations

import logging
from typing import Any

from engine.pipeline.reuse import collect_avoid_facts
from shared.config import get_settings
from shared.db import channels, queue

logger = logging.getLogger(__name__)

# 每筆候選帶去 series_context 的最近集數標題數量：對齊 shared/db/channels.py 的
# 契約註解（「該頻道最近 2~3 集標題」）。
_SERIES_CONTEXT_SIZE = 3

# avoid_facts 往回看的集數：比 series_context 多幾集。呼應只需要最近幾集的標題，
# 避重則要涵蓋更廣的事實池（collect_avoid_facts 內部還會再截到 12 條）。
# 一次查詢同時餵兩個用途，不多跑一趟 DB。
_AVOID_FACTS_LOOKBACK = 5


async def _build_message(candidate: dict[str, Any], deliver_date: str) -> dict[str, Any]:
    """把 pick_daily_topics 選出的一筆候選轉成完整 generate contract。

    同一次查詢餵兩個用途：series_context 取最近幾集的標題讓寫稿能呼應系列脈絡，
    avoid_facts 取更廣範圍的既有事實避免相鄰集重複——頻道是「同一主題連續出刊」，
    比舊的固定 slot 更容易撞同一批事實，所以避重不能省。
    """
    channel_id = candidate["channel_id"]
    recent = await channels.list_recent_channel_episodes(channel_id, _AVOID_FACTS_LOOKBACK)
    return {
        "big_topic": candidate["topic"],
        "canonical_topic": candidate["canonical_topic"],
        "angle": candidate["angle"],
        "topic_type": candidate["topic_type"],
        "length_tier": candidate["length_tier"],
        "cefr": candidate["cefr_level"],
        # 公開集來源（不是用戶下單）；upsert_episode_node 用 source != "specified" 推 is_free=true
        "source": "daily_batch",
        "deliver_date": deliver_date,
        "user_ids": [],  # 不建立 delivery，留給 reuse L1 邏輯接手
        "cluster_id": None,
        "avoid_facts": collect_avoid_facts(recent),
        "channel_id": str(channel_id),
        "channel_topic_id": str(candidate["topic_id"]),
        "series_context": [ep["title"] for ep in recent[:_SERIES_CONTEXT_SIZE]],
    }


async def build_daily_messages(deliver_date: str) -> list[dict[str, Any]]:
    """挑出今天要生成的頻道候選，組成 0..channel_daily_max_slots 筆 generate message。

    候選不足（甚至 0 筆）是合法結果，不是錯誤——「沒內容就不產」由
    pick_daily_topics 的 SQL 保證，這裡不補湊、不假設固定筆數。
    """
    settings = get_settings()
    candidates = await channels.pick_daily_topics(
        min_score=settings.channel_min_topic_score,
        max_slots=settings.channel_daily_max_slots,
    )
    return [await _build_message(c, deliver_date) for c in candidates]


async def enqueue_daily_batch(deliver_date: str) -> int:
    """原子 enqueue 當日頻道候選批次（0..channel_daily_max_slots 筆）。

    回傳 -1＝該 deliver_date 已被其他 worker claim（重複 control 訊息，這批
    message 完全沒被送出）；0＝正常完成但今天沒有合格候選，不產出；正整數＝
    正常完成並送出該筆數。

    只有確定真的送出後才把候選標記 scheduled：duplicate control 訊息若提早把
    候選標成 scheduled，會讓這些候選從此在 pick_daily_topics 裡消失，卻其實
    從未真的進過 generate 佇列——所以標記動作放在 send_daily_batch 回傳非
    -1、非 0 之後，用 message 本身帶的 channel_topic_id 回填，不另外重查一次
    candidates。

    send_daily_batch() 把 marker INSERT + N 筆 send 全收進 SQL function 的單一
    transaction；不應在此改成逐筆 queue.send()，會破壞 exactly-once 語意。
    """
    messages = await build_daily_messages(deliver_date)
    enqueued = await queue.send_daily_batch(deliver_date, messages)

    if enqueued == -1:
        logger.info("daily_podcast 該日期已被其他 worker 處理，略過 date=%s", deliver_date)
    elif enqueued == 0:
        logger.info("daily_podcast：今天沒有合格候選，不產出 date=%s", deliver_date)
    elif enqueued != len(messages):
        logger.error(
            "daily_podcast enqueue 筆數與候選數不符，回填全部略過避免誤標 scheduled "
            "date=%s enqueued=%d candidates=%d",
            deliver_date,
            enqueued,
            len(messages),
        )
    else:
        await _mark_topics_scheduled(messages, deliver_date)
        logger.info("daily_podcast enqueue 完成 date=%s count=%d", deliver_date, enqueued)

    return enqueued


async def _mark_topics_scheduled(messages: list[dict[str, Any]], deliver_date: str) -> None:
    """把已確定送出的候選標成 scheduled，避免明天又被 pick_daily_topics 挑中。

    單筆失敗只記 log 不中斷：訊息已經在佇列裡了，這裡 raise 只會讓整批 control
    重投、造成真正的重複生成，比漏標一筆更糟。
    """
    for msg in messages:
        try:
            await channels.update_topic_status(msg["channel_topic_id"], "scheduled")
        except Exception:
            logger.exception(
                "daily_podcast 回填 topic 狀態失敗，該候選明天可能重複生成 "
                "date=%s channel_topic_id=%s",
                deliver_date,
                msg["channel_topic_id"],
            )
