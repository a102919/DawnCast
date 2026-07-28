"""測試專用 DB fake 殼：取代各 router 測試檔逐字複製的 FakeCursor/FakeConnection。

各測試檔真正不同的地方只有 execute() 內的 SQL 分派邏輯（依 SQL 關鍵字 + params
回傳預置的 in-memory 列）；__aenter__/__aexit__、cursor()/commit() 這層殼完全
一樣。這裡把殼抽出來，子類只需覆寫 execute()（以及視需要在 __init__ 補充欄位，
例如 rowcount），再用 fake_connection(FakeConnection) 組出可直接 monkeypatch
進 router 的 connection 工廠。

典型用法：

    from tests._db_fakes import FakeConnection as _BaseFakeConnection
    from tests._db_fakes import FakeCursor as _BaseFakeCursor
    from tests._db_fakes import fake_connection

    class FakeCursor(_BaseFakeCursor):
        async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
            ...  # 依 sql 關鍵字分派，寫入 self._rows

    class FakeConnection(_BaseFakeConnection):
        def cursor(self, **_: object) -> FakeCursor:
            return FakeCursor()

    monkeypatch.setattr(some_router, "connection", fake_connection(FakeConnection))
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any


class FakeCursor:
    """假 async cursor 殼：SQL 分派邏輯留給子類覆寫 execute()。

    self._rows 型別故意放寬成 list[Any]：多數 router 用 row_factory=dict_row
    回傳 dict 列，少數（如 post_process.backfill_dict）用預設 cursor 回傳
    tuple 列，殼本身不預設任一種形狀。
    """

    def __init__(self) -> None:
        self._rows: list[Any] = []

    async def __aenter__(self) -> FakeCursor:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        self._rows = []

    async def fetchall(self) -> list[Any]:
        return self._rows

    async def fetchone(self) -> Any | None:
        return self._rows[0] if self._rows else None


class FakeConnection:
    """假 async connection 殼：cursor() 回傳 FakeCursor，子類覆寫換掉型別。"""

    def cursor(self, **_: object) -> FakeCursor:
        return FakeCursor()

    async def commit(self) -> None:
        return None


def fake_connection(
    connection_cls: type[FakeConnection] = FakeConnection,
) -> Callable[[], AbstractAsyncContextManager[FakeConnection]]:
    """回傳可重複呼叫、模擬 shared.db.pool.connection() 的 async context manager 工廠。"""

    @asynccontextmanager
    async def _connect() -> AsyncIterator[FakeConnection]:
        yield connection_cls()

    return _connect
