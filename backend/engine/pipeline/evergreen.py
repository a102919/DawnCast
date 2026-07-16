"""evergreen 兜底：對當天未交付者補一集常青集（PRD §7）。

把「黎明 SLA」與「生成成功率」解耦——就算夜間生成全掛，03:30 這一步仍保證
每個 user 都有東西可聽。insert_delivery 是 ON CONFLICT DO NOTHING，重跑冪等。
"""

from __future__ import annotations

import logging

from shared.db import repo

logger = logging.getLogger(__name__)


async def run_evergreen(deliver_date: str) -> int:
    """對未交付者全補常青集。回傳實際新增的交付數。

    挑不到任何常青集時略過該 user（記 warning），不讓單點缺料拖垮整批。
    """
    users = await repo.undelivered_users(deliver_date)
    delivered = 0
    for user_id in users:
        episode_id = await repo.pick_evergreen_episode(None)
        if episode_id is None:
            logger.warning("evergreen 兜底找不到常青集，user=%s 當天無交付", user_id)
            continue
        if await repo.insert_delivery(user_id, episode_id, deliver_date):
            delivered += 1
    logger.info("evergreen 兜底完成 deliver_date=%s 補 %d 筆", deliver_date, delivered)
    return delivered
