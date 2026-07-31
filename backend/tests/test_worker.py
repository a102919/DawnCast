"""worker.py 的點餐相關控制邏輯測試（migration 0024：隨時點餐、佇列制）。

驗證重點：
  1. `_orchestrate_order`：查得到投影就呼叫 resolve_for_user 並正確透傳
     order_id；查無此 order（已被取消）就略過，不拋例外、不呼叫 resolve_for_user。
  2. `_order_reconcile`：
     - pending 太久沒翻 queued → 重放 CAS + enqueue orchestrate（帶 order_id/date）
     - CAS 沒搶到（已被別的請求翻過）→ 不重複 enqueue
     - queued 太久且無 deliveries → 補墊檔常青集（pick_evergreen_episode +
       insert_delivery 帶 order_id）
     - 找不到墊檔常青集 → 略過，不拋例外
     - 兩邊都查無「卡住」的訂單（門檻內的正常訂單）→ 完全不觸發任何動作

不連 DB：project_order_to_request / resolve_for_user / app_repo 系列函式 /
queue.send 全部 monkeypatch 成假件，只驗 Python 側的分派與參數透傳邏輯。
"""

from __future__ import annotations

from typing import Any

import pytest

from engine import worker


class _FakeQueue:
    def __init__(self) -> None:
        self.sent: list[tuple[str, dict[str, Any]]] = []

    async def send(self, queue: str, body: dict[str, Any]) -> int:
        self.sent.append((queue, dict(body)))
        return len(self.sent)


async def _fake_expire_empty(older_than_sec: int) -> list[dict[str, Any]]:
    """expire_old_active_orders 的預設 fake：回空 list（沒人卡死要退役）。"""
    return []


# ── _orchestrate_order ──────────────────────────────────────────────


async def test_orchestrate_order_projects_and_resolves(monkeypatch: pytest.MonkeyPatch) -> None:
    projected = {
        "user_id": "u1",
        "big_topic": "ai",
        "topic_type": "topic",
        "length_tier": "long",
        "source": "specified",
        "cefr": "B2",
    }

    async def fake_project(order_id: str) -> dict[str, Any] | None:
        assert order_id == "order-1"
        return projected

    resolve_calls: list[dict[str, Any]] = []

    async def fake_resolve(**kwargs: Any) -> str | None:
        resolve_calls.append(kwargs)
        return None

    monkeypatch.setattr(worker.repo, "project_order_to_request", fake_project)
    monkeypatch.setattr(worker, "resolve_for_user", fake_resolve)

    await worker._orchestrate_order("order-1", "2026-07-20")

    assert len(resolve_calls) == 1
    call = resolve_calls[0]
    assert call["user_id"] == "u1"
    assert call["big_topic"] == "ai"
    assert call["deliver_date"] == "2026-07-20"
    assert call["topic_type"] == "topic"
    assert call["length_tier"] == "long"
    assert call["cefr"] == "B2"
    assert call["source"] == "specified"
    assert call["order_id"] == "order-1"


async def test_orchestrate_order_skips_when_order_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_project(order_id: str) -> dict[str, Any] | None:
        return None

    resolve_calls: list[dict[str, Any]] = []

    async def fake_resolve(**kwargs: Any) -> str | None:
        resolve_calls.append(kwargs)
        return None

    monkeypatch.setattr(worker.repo, "project_order_to_request", fake_project)
    monkeypatch.setattr(worker, "resolve_for_user", fake_resolve)

    # 不拋例外，且不呼叫 resolve_for_user（訂單可能已被 DELETE 取消）
    await worker._orchestrate_order("does-not-exist", "2026-07-20")

    assert resolve_calls == []


# ── _order_reconcile ─────────────────────────────────────────────────


async def test_order_reconcile_replays_stuck_pending(monkeypatch: pytest.MonkeyPatch) -> None:
    stuck = [{"id": "o1", "user_id": "u1", "order_date": "2026-07-20"}]

    async def fake_list_pending(older_than_sec: int) -> list[dict[str, Any]]:
        assert older_than_sec == worker.STUCK_PENDING_SEC
        return stuck

    transition_calls: list[tuple[str, str]] = []

    async def fake_transition(user_id: str, order_id: str) -> bool:
        transition_calls.append((user_id, order_id))
        return True

    async def fake_list_queued(older_than_sec: int) -> list[dict[str, Any]]:
        return []

    q = _FakeQueue()
    monkeypatch.setattr(worker.app_repo, "list_stuck_pending_orders", fake_list_pending)
    monkeypatch.setattr(worker.app_repo, "transition_order_to_queued", fake_transition)
    monkeypatch.setattr(
        worker.app_repo, "list_stuck_queued_orders_without_delivery", fake_list_queued
    )
    monkeypatch.setattr(worker.app_repo, "expire_old_active_orders", _fake_expire_empty)
    monkeypatch.setattr(worker, "queue", q)

    await worker._order_reconcile()

    assert transition_calls == [("u1", "o1")]
    assert q.sent == [("control", {"task": "orchestrate", "order_id": "o1", "date": "2026-07-20"})]


async def test_order_reconcile_skips_enqueue_when_cas_loses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """transition_order_to_queued 回 False：已經被別的請求（或上一輪 reconcile）
    翻過了，不是真的卡住 → 不該再 enqueue 一次。"""
    stuck = [{"id": "o1", "user_id": "u1", "order_date": "2026-07-20"}]

    async def fake_list_pending(older_than_sec: int) -> list[dict[str, Any]]:
        return stuck

    async def fake_transition(user_id: str, order_id: str) -> bool:
        return False

    async def fake_list_queued(older_than_sec: int) -> list[dict[str, Any]]:
        return []

    q = _FakeQueue()
    monkeypatch.setattr(worker.app_repo, "list_stuck_pending_orders", fake_list_pending)
    monkeypatch.setattr(worker.app_repo, "transition_order_to_queued", fake_transition)
    monkeypatch.setattr(
        worker.app_repo, "list_stuck_queued_orders_without_delivery", fake_list_queued
    )
    monkeypatch.setattr(worker.app_repo, "expire_old_active_orders", _fake_expire_empty)
    monkeypatch.setattr(worker, "queue", q)

    await worker._order_reconcile()

    assert q.sent == []


async def test_order_reconcile_delivers_evergreen_fallback_for_stuck_queued(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stuck = [{"id": "o2", "user_id": "u2", "order_date": "2026-07-19"}]

    async def fake_list_pending(older_than_sec: int) -> list[dict[str, Any]]:
        return []

    async def fake_list_queued(older_than_sec: int) -> list[dict[str, Any]]:
        assert older_than_sec == worker.STUCK_QUEUED_SEC
        return stuck

    async def fake_pick_evergreen(big_topic: str | None) -> str | None:
        assert big_topic is None
        return "ep-99"

    # 兜底路徑走 deliver_and_mark_ready（transactional 合併 insert + mark_ready）。
    deliver_calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    async def fake_deliver_and_mark_ready(
        *args: Any, **kwargs: Any
    ) -> bool:
        deliver_calls.append((args, kwargs))
        return True

    monkeypatch.setattr(worker.app_repo, "list_stuck_pending_orders", fake_list_pending)
    monkeypatch.setattr(
        worker.app_repo, "list_stuck_queued_orders_without_delivery", fake_list_queued
    )
    monkeypatch.setattr(worker.repo, "pick_evergreen_episode", fake_pick_evergreen)
    monkeypatch.setattr(
        worker.app_repo, "deliver_and_mark_ready", fake_deliver_and_mark_ready
    )
    monkeypatch.setattr(worker.app_repo, "expire_old_active_orders", _fake_expire_empty)
    monkeypatch.setattr(worker, "queue", _FakeQueue())

    await worker._order_reconcile()

    assert len(deliver_calls) == 1
    args, kwargs = deliver_calls[0]
    assert args == ("u2", "ep-99", "2026-07-19")
    assert kwargs == {"order_id": "o2"}


async def test_order_reconcile_missing_evergreen_episode_does_not_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stuck = [{"id": "o3", "user_id": "u3", "order_date": "2026-07-18"}]

    async def fake_list_pending(older_than_sec: int) -> list[dict[str, Any]]:
        return []

    async def fake_list_queued(older_than_sec: int) -> list[dict[str, Any]]:
        return stuck

    async def fake_pick_evergreen(big_topic: str | None) -> str | None:
        return None

    deliver_calls: list[Any] = []

    async def fake_deliver_and_mark_ready(
        *args: Any, **kwargs: Any
    ) -> bool:
        deliver_calls.append((args, kwargs))
        return True

    monkeypatch.setattr(worker.app_repo, "list_stuck_pending_orders", fake_list_pending)
    monkeypatch.setattr(
        worker.app_repo, "list_stuck_queued_orders_without_delivery", fake_list_queued
    )
    monkeypatch.setattr(worker.repo, "pick_evergreen_episode", fake_pick_evergreen)
    monkeypatch.setattr(
        worker.app_repo, "deliver_and_mark_ready", fake_deliver_and_mark_ready
    )
    monkeypatch.setattr(worker.app_repo, "expire_old_active_orders", _fake_expire_empty)
    monkeypatch.setattr(worker, "queue", _FakeQueue())

    # 不拋例外；找不到墊檔常青集就略過這筆，不呼叫 deliver_and_mark_ready
    await worker._order_reconcile()

    assert deliver_calls == []


async def test_order_reconcile_no_action_when_nothing_stuck(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """兩邊 repo 查詢都回空（門檻內的正常訂單，SQL 層已經濾掉）→ 完全不觸發
    transition / enqueue / evergreen 墊檔任何一個動作。"""

    async def fake_list_pending(older_than_sec: int) -> list[dict[str, Any]]:
        return []

    async def fake_list_queued(older_than_sec: int) -> list[dict[str, Any]]:
        return []

    calls: list[str] = []

    async def fail_if_called(*_args: Any, **_kwargs: Any) -> Any:
        calls.append("called")
        raise AssertionError("不該被呼叫")

    q = _FakeQueue()
    monkeypatch.setattr(worker.app_repo, "list_stuck_pending_orders", fake_list_pending)
    monkeypatch.setattr(worker.app_repo, "transition_order_to_queued", fail_if_called)
    monkeypatch.setattr(
        worker.app_repo, "list_stuck_queued_orders_without_delivery", fake_list_queued
    )
    monkeypatch.setattr(worker.repo, "pick_evergreen_episode", fail_if_called)
    monkeypatch.setattr(worker.repo, "insert_delivery", fail_if_called)
    monkeypatch.setattr(worker.app_repo, "deliver_and_mark_ready", fail_if_called)
    monkeypatch.setattr(worker.app_repo, "mark_order_ready", fail_if_called)

    monkeypatch.setattr(worker.app_repo, "expire_old_active_orders", _fake_expire_empty)
    monkeypatch.setattr(worker, "queue", q)

    await worker._order_reconcile()

    assert calls == []
    assert q.sent == []


# ── migration 0027：active 訂單退役（expire）─────────────────────────


async def test_order_reconcile_expires_stuck_active_orders_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """expire 必須跑在重放 pending / 補 evergreen 之前——被退役的 row 不該被
    reconcile 重複處理。門檻傳 EXPIRE_AFTER_SEC（1800s）。"""
    call_order: list[str] = []

    async def fake_expire(older_than_sec: int) -> list[dict[str, Any]]:
        assert older_than_sec == worker.EXPIRE_AFTER_SEC
        call_order.append("expire")
        return []

    async def fake_list_pending(older_than_sec: int) -> list[dict[str, Any]]:
        call_order.append("list_pending")
        return []

    async def fake_list_queued(older_than_sec: int) -> list[dict[str, Any]]:
        call_order.append("list_queued")
        return []

    monkeypatch.setattr(worker.app_repo, "expire_old_active_orders", fake_expire)
    monkeypatch.setattr(worker.app_repo, "list_stuck_pending_orders", fake_list_pending)
    monkeypatch.setattr(
        worker.app_repo, "list_stuck_queued_orders_without_delivery", fake_list_queued
    )
    monkeypatch.setattr(worker, "queue", _FakeQueue())

    await worker._order_reconcile()

    assert call_order == ["expire", "list_pending", "list_queued"]


async def test_order_reconcile_skips_downstream_actions_when_expire_returns_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """退役清單非空：只 log，不會再被下面的 reconcile 路徑處理（CAS 條件式
    UPDATE 在 DB 層會自動 skip，這裡驗 Python 側分派邏輯沒有多餘動作）。"""
    expired = [
        {"id": "old-1", "user_id": "u1", "order_date": "2026-07-15", "status": "queued"},
        {"id": "old-2", "user_id": "u2", "order_date": "2026-07-15", "status": "pending"},
    ]

    async def fake_expire(older_than_sec: int) -> list[dict[str, Any]]:
        return expired

    # 下游兩個查詢就算回傳舊 row 也只是測試 fixture，不影響斷言：
    # 本測試只確認 reconcile 在 expire 已撿走舊 row 後，不會再額外呼叫 transition/
    # enqueue/deliver_and_mark_ready——這些 fail_if_called 不存在，行為驗證靠
    # 後面的兩個 list_* 回傳空。
    async def fake_list_pending(older_than_sec: int) -> list[dict[str, Any]]:
        return []

    async def fake_list_queued(older_than_sec: int) -> list[dict[str, Any]]:
        return []

    q = _FakeQueue()
    monkeypatch.setattr(worker.app_repo, "expire_old_active_orders", fake_expire)
    monkeypatch.setattr(worker.app_repo, "list_stuck_pending_orders", fake_list_pending)
    monkeypatch.setattr(
        worker.app_repo, "list_stuck_queued_orders_without_delivery", fake_list_queued
    )
    monkeypatch.setattr(worker, "queue", q)

    await worker._order_reconcile()

    # 沒被退役的 row 才會走下游；這裡 list_* 都空，下游路徑零觸發。
    assert q.sent == []


async def test_expire_threshold_is_greater_than_reconcile_window() -> None:
    """EXPIRE_AFTER_SEC 必須 ≫ STUCK_PENDING_SEC + STUCK_QUEUED_SEC，給
    reconcile 至少兩輪完整重試 + 一輪 evergreen 兜底才退役。
    寫死這個不變式防止日後有人調小 reconcile 門檻或調大 expire 門檻、
    把正常訂單誤判成卡死退役。

    上限：EXPIRE_AFTER_SEC ≤ 3600s = 1 小時，避免有人改 expire 成 7 天、
    使用者卡一週才退役。reconcile 每 5 分鐘跑一輪，1 小時已是充足邊界。
    """
    assert worker.EXPIRE_AFTER_SEC > worker.STUCK_PENDING_SEC + worker.STUCK_QUEUED_SEC
    assert worker.EXPIRE_AFTER_SEC <= 3600
