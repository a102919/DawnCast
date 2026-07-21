"""一次性 / 部署時跑的 migration runner。

用 `supabase_admin`（self-host Zeabur 預設的超級使用者）連進 db，
按檔名順序跑 `backend/db/migrations/0001_init.sql` → `0009_user_activity.sql`。

設計考量：
- 用 superuser 跑是為了 `pgmq.create()` / `cron.schedule()` 等需要高權限的函式。
  application runtime 用 `postgres` 一般角色跑就夠（自己 DDL/DML 不踩權限雷）。
- Idempotent：每支 SQL 內部已有 `if not exists` / `create or replace`；本檔不擋重跑。
- 失敗立即 raise，第一支錯就不跑後面（部署 v1 不用 partial recovery）。
- 不在 `schema_migrations` 建表（避免與 Supabase 自帶的 init migration 衝突）；
  部署 SOP 用檔名排序作為進展依據。

使用方式：
    # Zeabur 部署時由 backup/init container 跑一次
    POSTGRES_HOST=db POSTGRES_PORT=5432 \\
    POSTGRES_USER=supabase_admin POSTGRES_PASSWORD=... POSTGRES_DB=postgres \\
    uv run python -m scripts.apply_migrations

    # 本機跑（docker-compose）
    POSTGRES_HOST=localhost POSTGRES_USER=supabase_admin \\
    POSTGRES_PASSWORD=postgres uv run python -m scripts.apply_migrations
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import psycopg

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "db" / "migrations"


def _discover() -> list[Path]:
    """依檔名升冪排序列出所有 *.sql。"""
    if not MIGRATIONS_DIR.is_dir():
        raise RuntimeError(f"migrations 目錄不存在：{MIGRATIONS_DIR}")
    return sorted(p for p in MIGRATIONS_DIR.glob("*.sql"))


def _apply(conn: psycopg.Connection, sql_path: Path) -> None:
    print(f"→ applying {sql_path.name}", flush=True)
    sql = sql_path.read_text(encoding="utf-8")
    with conn.cursor() as cur:
        cur.execute(sql)
    # 中間 commit（DDL/DML 都落地）
    conn.commit()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只列出將要跑的檔案，不實際執行",
    )
    args = parser.parse_args()

    files = _discover()
    if not files:
        print(f"在 {MIGRATIONS_DIR} 找不到 *.sql，沒事可做。", flush=True)
        return 0

    print(f"發現 {len(files)} 支 migrations：", flush=True)
    for p in files:
        print(f"  - {p.name}", flush=True)

    if args.dry_run:
        return 0

    user = os.environ.get("POSTGRES_USER") or os.environ.get("PGUSER") or "postgres"
    password = os.environ.get("POSTGRES_PASSWORD") or os.environ.get("PGPASSWORD")
    if not password:
        print("POSTGRES_PASSWORD 必設（migration runner 需要 superuser 權限）。", file=sys.stderr)
        return 2

    dsn = (
        f"host={os.environ.get('POSTGRES_HOST', 'localhost')} "
        f"port={os.environ.get('POSTGRES_PORT', '5432')} "
        f"user={user} "
        f"password={password} "
        f"dbname={os.environ.get('POSTGRES_DB', 'postgres')} "
        f"application_name=dawncast_migrate"
    )

    print(f"\n連線到 {user}@...（讀 env 略）", flush=True)

    with psycopg.connect(dsn, autocommit=False) as conn:
        for path in files:
            try:
                _apply(conn, path)
            except Exception as exc:
                print(f"\n✗ {path.name} 失敗：{exc}", file=sys.stderr)
                conn.rollback()
                return 1

    print(f"\n✓ {len(files)} 支 migrations 全部完成", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
