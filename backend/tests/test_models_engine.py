"""shared/models/engine.py 的純資料結構測試：EntryMode → TopicType 對映。"""

from __future__ import annotations

from typing import get_args

from shared.models import ENTRY_MODE_TO_TOPIC_TYPE, EntryMode, TopicType


def test_entry_mode_to_topic_type_covers_every_entry_mode() -> None:
    # 對映表窮盡 EntryMode 所有值——少一個代表 project_orders_to_requests
    # 會在 reuse_repo.py 撞 KeyError（比舊版 SQL CASE 的隱性 else 更早炸開）。
    assert set(ENTRY_MODE_TO_TOPIC_TYPE.keys()) == set(get_args(EntryMode))


def test_entry_mode_to_topic_type_maps_each_value_correctly() -> None:
    # news/skill 是使用者值域與引擎值域同名的直通；topic→product、
    # knowledge→evergreen 是唯一需要真的轉換的兩個值。
    assert ENTRY_MODE_TO_TOPIC_TYPE["news"] == "news"
    assert ENTRY_MODE_TO_TOPIC_TYPE["topic"] == "product"
    assert ENTRY_MODE_TO_TOPIC_TYPE["knowledge"] == "evergreen"
    assert ENTRY_MODE_TO_TOPIC_TYPE["skill"] == "skill"


def test_entry_mode_to_topic_type_values_are_valid_topic_types() -> None:
    valid_topic_types = set(get_args(TopicType))
    assert set(ENTRY_MODE_TO_TOPIC_TYPE.values()) <= valid_topic_types
