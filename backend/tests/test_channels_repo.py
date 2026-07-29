"""shared/db/channels.py 單元測試。

純 Python mock 測試，不連真 DB：monkeypatch channels.connection 換成假
connection/cursor（沿用 tests/_db_fakes.py 的殼，模式同 test_api.py），
捕捉每次 execute() 收到的 SQL 文字（正規化空白後）與 params，驗證：
  (a) SQL 形狀正確（關鍵字、動態子句都有出現）
  (b) params 綁定順序/內容正確
  (c) 回傳值對 DB row 的轉換邏輯正確（尤其「實際筆數」與「1 起跳」這類算術）

不驗 Postgres 本身的執行語意（那是 migration + 型別的責任），只驗 Python 這層
組出來的 SQL 契約沒有漂移。
"""

from __future__ import annotations

from typing import Any

import pytest

from shared.db import channels
from tests._db_fakes import FakeConnection as _BaseFakeConnection
from tests._db_fakes import FakeCursor as _BaseFakeCursor
from tests._db_fakes import fake_connection


class _RecordingCursor(_BaseFakeCursor):
    """捕捉每次 execute() 的 SQL（正規化空白）與 params；回放 _install() 設定好的假資料列。

    channels.py 每個函式在單一 connection block 內只發一次 execute()，
    用類別層級屬性存「最後一次呼叫」即可，_install() 每個測試開頭都會重置。
    """

    calls: list[tuple[str, Any]] = []
    canned_rows: list[dict[str, Any]] = []
    canned_rowcount: int = 0

    async def execute(self, sql: str, params: Any = None) -> None:
        normalized = " ".join(sql.split())
        type(self).calls.append((normalized, params))
        self._rows = list(type(self).canned_rows)
        self.rowcount = type(self).canned_rowcount


class _RecordingConnection(_BaseFakeConnection):
    def cursor(self, **_: object) -> _RecordingCursor:
        return _RecordingCursor()


def _install(
    monkeypatch: pytest.MonkeyPatch,
    *,
    rows: list[dict[str, Any]] | None = None,
    rowcount: int = 0,
) -> type[_RecordingCursor]:
    """monkeypatch channels.connection，並重置這次測試要回放的假資料。"""
    _RecordingCursor.calls = []
    _RecordingCursor.canned_rows = rows or []
    _RecordingCursor.canned_rowcount = rowcount
    monkeypatch.setattr(channels, "connection", fake_connection(_RecordingConnection))
    return _RecordingCursor


def _last_call(cursor_cls: type[_RecordingCursor]) -> tuple[str, Any]:
    assert cursor_cls.calls, "預期至少發生一次 execute()，實際沒有"
    return cursor_cls.calls[-1]


# ── channels 本體 ────────────────────────────────────────────────────


async def test_list_channels_computes_counts_via_subquery_not_n_plus_1(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """episode_count / candidate_count 必須是同一條 SELECT 的相關子查詢，
    不是 Python 端另外對每個頻道各發一次查詢（N+1）。"""
    row = {"id": "c1", "slug": "ai-daily", "episode_count": 3, "candidate_count": 2}
    cur_cls = _install(monkeypatch, rows=[row])

    result = await channels.list_channels(status="active")

    sql, params = _last_call(cur_cls)
    assert "episode_count" in sql
    assert "candidate_count" in sql
    assert "from public.channels c" in sql
    assert params == {"status": "active"}
    assert result == [row]
    assert len(cur_cls.calls) == 1  # 只有一次 execute，佐證沒有 N+1


async def test_list_channels_no_status_filter_passes_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """不帶 status → 參數傳 None，交給 SQL 的 `is null` 分支放行全部列。"""
    cur_cls = _install(monkeypatch, rows=[])
    await channels.list_channels()
    _, params = _last_call(cur_cls)
    assert params == {"status": None}


async def test_get_channel_found_and_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    cur_cls = _install(monkeypatch, rows=[{"id": "c1", "slug": "ai-daily"}])
    found = await channels.get_channel("c1")
    assert found == {"id": "c1", "slug": "ai-daily"}
    sql, params = _last_call(cur_cls)
    assert "where c.id = %s" in sql
    assert params == ("c1",)

    _install(monkeypatch, rows=[])
    missing = await channels.get_channel("nope")
    assert missing is None


async def test_get_channel_by_slug(monkeypatch: pytest.MonkeyPatch) -> None:
    cur_cls = _install(monkeypatch, rows=[{"id": "c1", "slug": "ai-daily"}])
    found = await channels.get_channel_by_slug("ai-daily")
    assert found == {"id": "c1", "slug": "ai-daily"}
    sql, params = _last_call(cur_cls)
    assert "where c.slug = %s" in sql
    assert params == ("ai-daily",)


async def test_create_channel_returns_id_with_positional_params_in_column_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cur_cls = _install(monkeypatch, rows=[{"id": "new-id"}])

    new_id = await channels.create_channel(
        slug="ai-daily",
        name="AI Daily",
        theme_prompt="每天一則 AI 應用案例",
        topic="tech",
    )

    assert new_id == "new-id"
    sql, params = _last_call(cur_cls)
    assert "insert into public.channels" in sql
    assert "returning id" in sql
    # 沒明講的欄位要落到 create_channel 的預設值（對齊 migration 0021 DDL 預設）。
    assert params == (
        "ai-daily",
        "AI Daily",
        None,
        "每天一則 AI 應用案例",
        "tech",
        "evergreen",
        "medium",
        "B1",
        3,
        "active",
    )


async def test_create_channel_raises_when_no_row_returned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(monkeypatch, rows=[])
    with pytest.raises(RuntimeError):
        await channels.create_channel(
            slug="x", name="X", theme_prompt="p", topic="tech"
        )


async def test_update_channel_rejects_unknown_field_without_querying(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cur_cls = _install(monkeypatch)
    with pytest.raises(ValueError):
        await channels.update_channel("c1", not_a_real_column="x")
    assert cur_cls.calls == []  # fail fast：驗證白名單發生在發查詢之前


async def test_update_channel_no_fields_returns_false_without_querying(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cur_cls = _install(monkeypatch)
    result = await channels.update_channel("c1")
    assert result is False
    assert cur_cls.calls == []  # 沒欄位可改，不該發任何 SQL


async def test_update_channel_builds_dynamic_set_clause(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cur_cls = _install(monkeypatch, rowcount=1)

    ok = await channels.update_channel("c1", status="paused", name="New Name")

    assert ok is True
    sql, params = _last_call(cur_cls)
    assert "update public.channels set" in sql
    assert "status" in sql and "%(status)s" in sql
    assert "name" in sql and "%(name)s" in sql
    assert "updated_at = now()" in sql
    assert "where id = %(channel_id)s" in sql
    assert params == {"status": "paused", "name": "New Name", "channel_id": "c1"}


async def test_update_channel_returns_false_when_id_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(monkeypatch, rowcount=0)
    ok = await channels.update_channel("missing", status="paused")
    assert ok is False


async def test_set_channel_cover(monkeypatch: pytest.MonkeyPatch) -> None:
    cur_cls = _install(monkeypatch)
    await channels.set_channel_cover("c1", "covers/c1.jpg")
    sql, params = _last_call(cur_cls)
    assert "update public.channels set cover_r2_key" in sql
    assert params == ("covers/c1.jpg", "c1")


# ── 選題庫 ──────────────────────────────────────────────────────────


async def test_list_channel_topics(monkeypatch: pytest.MonkeyPatch) -> None:
    row = {"id": "t1", "channel_id": "c1", "canonical_topic": "AI agents", "score": 0.8}
    cur_cls = _install(monkeypatch, rows=[row])

    result = await channels.list_channel_topics("c1", status="candidate")

    sql, params = _last_call(cur_cls)
    assert "from public.channel_topics" in sql
    assert "order by score desc" in sql
    assert params == {"channel_id": "c1", "status": "candidate"}
    assert result == [row]


async def test_insert_channel_topics_returns_actual_inserted_count_not_input_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """3 筆候選、其中 1 筆撞 on conflict 被吃掉 → 回傳 2，不是 3。"""
    cur_cls = _install(monkeypatch, rows=[{"id": "t1"}, {"id": "t2"}])

    candidates = [
        {"canonical_topic": "AI agents", "angle": "定義", "score": 0.9},
        {"canonical_topic": "LLM eval", "angle": "應用場景", "rationale": "熱門"},
        {"canonical_topic": "AI agents", "angle": "對比"},  # 撞重複主題
    ]
    inserted = await channels.insert_channel_topics("c1", candidates)

    assert inserted == 2  # 來自 RETURNING 實際列數，不是 len(candidates)
    sql, params = _last_call(cur_cls)
    assert "on conflict (channel_id, lower(canonical_topic)) do nothing" in sql
    assert "returning id" in sql
    assert sql.count("%s") == 18  # 3 筆 candidate × 6 欄位
    assert len(params) == 18
    # 第一筆的欄位順序：channel_id, canonical_topic, angle, rationale, score, parent_episode_id
    assert list(params[:6]) == ["c1", "AI agents", "定義", None, 0.9, None]


async def test_insert_channel_topics_empty_list_returns_0_without_querying(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cur_cls = _install(monkeypatch)
    inserted = await channels.insert_channel_topics("c1", [])
    assert inserted == 0
    assert cur_cls.calls == []


async def test_update_topic_status_published_coalesces_episode_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cur_cls = _install(monkeypatch, rowcount=1)

    ok = await channels.update_topic_status("t1", "published", episode_id="ep-1")

    assert ok is True
    sql, params = _last_call(cur_cls)
    assert "set status = %s" in sql
    assert "episode_id = coalesce(%s, episode_id)" in sql
    assert "decided_at = now()" in sql
    assert params == ("published", "ep-1", "t1")


async def test_update_topic_status_rejected_keeps_episode_id_untouched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cur_cls = _install(monkeypatch, rowcount=1)
    await channels.update_topic_status("t1", "rejected")
    _, params = _last_call(cur_cls)
    assert params == ("rejected", None, "t1")  # None → coalesce 保留既有 episode_id


async def test_update_topic_status_not_found_returns_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(monkeypatch, rowcount=0)
    ok = await channels.update_topic_status("missing", "rejected")
    assert ok is False


async def test_count_candidates(monkeypatch: pytest.MonkeyPatch) -> None:
    cur_cls = _install(monkeypatch, rows=[{"n": 3}])
    n = await channels.count_candidates("c1")
    assert n == 3
    sql, params = _last_call(cur_cls)
    assert "status = 'candidate'" in sql
    assert params == ("c1",)


async def test_count_candidates_no_row_defensive_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # count(*) 實務上必回一列；這裡只驗防禦性 fallback 分支不會炸。
    _install(monkeypatch, rows=[])
    n = await channels.count_candidates("c1")
    assert n == 0


# ── 選題所需的頻道歷史 ────────────────────────────────────────────────


async def test_list_recent_channel_episodes(monkeypatch: pytest.MonkeyPatch) -> None:
    row = {"slug": "ep-1", "title": "T1", "angle": "定義", "extracted_facts": []}
    cur_cls = _install(monkeypatch, rows=[row])

    result = await channels.list_recent_channel_episodes("c1", limit=5)

    sql, params = _last_call(cur_cls)
    assert "from public.episodes" in sql
    assert "where channel_id = %s" in sql
    assert "order by created_at desc" in sql
    assert params == ("c1", 5)
    assert result == [row]


# ── 生產端排程查詢：機制核心 ───────────────────────────────────────────


async def test_pick_daily_topics_sql_has_distinct_on_and_starvation_factor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """鎖死 pick_daily_topics 的三個機制：distinct on（同頻道一天最多一集）、
    飢餓因子 least(3.0, ...)、以及照實回傳（不強迫湊滿 max_slots）。"""
    row = {"topic_id": "t1", "channel_id": "c1", "priority": 1.2}
    cur_cls = _install(monkeypatch, rows=[row])

    result = await channels.pick_daily_topics(min_score=0.6, max_slots=4)

    sql, params = _last_call(cur_cls)
    assert "distinct on (c.id)" in sql
    assert "least(3.0," in sql
    assert "ct.status = 'candidate'" in sql
    assert "c.status = 'active'" in sql
    assert "order by priority desc limit" in sql
    assert params == (0.6, 4)
    assert result == [row]  # 候選不足時原樣回傳，不補湊


async def test_pick_daily_topics_returns_fewer_than_max_slots_when_understocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """候選庫存不足 max_slots 時（甚至 0 筆）照實回傳，不是錯誤。"""
    _install(monkeypatch, rows=[])
    result = await channels.pick_daily_topics(min_score=0.6, max_slots=4)
    assert result == []


# ── 生成完成後回填 ───────────────────────────────────────────────────


async def test_mark_channel_published(monkeypatch: pytest.MonkeyPatch) -> None:
    cur_cls = _install(monkeypatch)
    await channels.mark_channel_published("c1", "2026-07-29")
    sql, params = _last_call(cur_cls)
    assert "greatest(coalesce(last_published_at, %s::date), %s::date)" in sql
    assert params == ("2026-07-29", "2026-07-29", "c1")


async def test_next_episode_no_starts_from_0_returns_1(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """該頻道尚無任何集數：coalesce(max(episode_no), 0) + 1 → 1。

    fake 不會真的跑 Postgres 聚合，這裡直接餵「0 筆情境」該有的算術結果
    （1），並額外斷言 SQL 文字真的長這個算式，兩者合起來才真的驗到
    「從 0 起算」這件事，不是只驗 Python 端把 canned 值原封傳回。
    """
    cur_cls = _install(monkeypatch, rows=[{"next_no": 1}])

    result = await channels.next_episode_no("c1")

    assert result == 1
    sql, params = _last_call(cur_cls)
    assert "coalesce(max(episode_no), 0) + 1" in sql
    assert params == ("c1",)


async def test_next_episode_no_continues_sequence(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, rows=[{"next_no": 8}])
    result = await channels.next_episode_no("c1")
    assert result == 8


async def test_next_episode_no_no_row_defensive_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 純量 aggregate SELECT 實務上必回一列；這裡只驗防禦性 fallback 不會炸。
    _install(monkeypatch, rows=[])
    result = await channels.next_episode_no("c1")
    assert result == 1


# ── 使用者訂閱（user_channel_subscriptions）──────────────────────────


async def test_subscribe_inserts_with_on_conflict_do_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cur_cls = _install(monkeypatch)
    await channels.subscribe("u1", "c1")
    sql, params = _last_call(cur_cls)
    assert "insert into public.user_channel_subscriptions" in sql
    assert "on conflict do nothing" in sql
    assert params == ("u1", "c1")


async def test_unsubscribe_deletes_by_composite_key(monkeypatch: pytest.MonkeyPatch) -> None:
    cur_cls = _install(monkeypatch)
    await channels.unsubscribe("u1", "c1")
    sql, params = _last_call(cur_cls)
    assert "delete from public.user_channel_subscriptions" in sql
    assert "where user_id = %s and channel_id = %s" in sql
    assert params == ("u1", "c1")


async def test_list_subscribed_channels_joins_subscriptions_no_status_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """刻意不篩 status='active'：使用者主動追蹤的頻道被封存/暫停不該悄悄消失
    （見函式 docstring）。"""
    row = {"id": "c1", "slug": "ai-daily", "episode_count": 3}
    cur_cls = _install(monkeypatch, rows=[row])

    result = await channels.list_subscribed_channels("u1")

    sql, params = _last_call(cur_cls)
    assert "join public.user_channel_subscriptions s on s.channel_id = c.id" in sql
    assert "where s.user_id = %s" in sql
    assert "status" not in sql.split("where")[1].split("order")[0]  # where 子句沒有 status 篩選
    assert params == ("u1",)
    assert result == [row]
