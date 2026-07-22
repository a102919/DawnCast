"""psycopg3 連線池。FastAPI（lifespan）與 worker 共用。

所有 SQL 一律參數化（禁字串拼接）。pgvector 型別在開池時註冊。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from shared.config import get_settings

_pool: AsyncConnectionPool | None = None


async def _configure(conn: AsyncConnection) -> None:
    conn.row_factory = dict_row  # type: ignore[assignment]
    # pgvector 型別註冊（讓 vector 欄位以 list[float] 進出）
    try:
        from pgvector.psycopg import register_vector_async  # type: ignore[import-untyped]

        await register_vector_async(conn)
    except Exception:
        # pgvector 未安裝於某些純 CRUD 環境時不致命；向量功能 V2 才上主流程
        pass


async def open_pool() -> AsyncConnectionPool:
    global _pool
    if _pool is None:
        settings = get_settings()
        # TCP keepalive（透傳給 libpq）：connection 在 pool 內 idle 超過
        # keepalives_idle 秒後，socket 開始送 TCP keepalive probe，避免 Zeabur
        # 內網 NAT / firewall 把長時間無流量的 connection 砍掉（worker polling
        # 主迴圈會反覆 acquire/release，pool 內 conn 大部分時間是 idle 狀態，
        # 沒有 keepalive 就會被中間節點 silent drop → 下次 query 撞
        # 'server closed the connection unexpectedly'）。
        # ponytail: keepalives_interval * keepalives_count 是最終放棄前的等待時間；
        #   30s + 10s*3 = 60s 內一定會探到，遠小於典型 NAT idle timeout（5min）。
        # libpq keepalives_* 透過 psycopg_pool.AsyncConnectionPool(kwargs=...) 透傳
        # 給每個 psycopg.AsyncConnection.connect()；直接放 kwargs 是 type-safe 寫法。
        _pool = AsyncConnectionPool(
            conninfo=settings.database_url,
            min_size=settings.db_pool_min,
            max_size=settings.db_pool_max,
            configure=_configure,
            open=False,
            kwargs={
                "keepalives": 1,
                "keepalives_idle": 30,
                "keepalives_interval": 10,
                "keepalives_count": 3,
            },
        )
        await _pool.open()
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


@asynccontextmanager
async def connection() -> AsyncIterator[AsyncConnection]:
    pool = await open_pool()
    async with pool.connection() as conn:
        yield conn
