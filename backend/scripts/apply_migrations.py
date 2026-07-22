"""一次性 / 部署時跑的 migration runner。

用 `supabase_admin`（self-host Zeabur 預設的超級使用者）連進 db，
按檔名順序跑 `backend/scripts/migrations/0001_init.sql` → `0009_user_activity.sql`。

設計考量：
- 用 superuser 跑是為了 `pgmq.create()` / `cron.schedule()` 等需要高權限的函式。
  application runtime 用 `postgres` 一般角色跑就夠（自己 DDL/DML 不踩權限雷）。
- Idempotent：每支 SQL 內部已有 `if not exists` / `create or replace`；本檔不擋重跑。
- 失敗立即 raise，第一支錯就不跑後面（部署 v1 不用 partial recovery）。
- 不在 `schema_migrations` 建表（避免與 Supabase 自帶的 init migration 衝突）；
  部署 SOP 用檔名排序作為進展依據。
- migrations/ 放在 scripts/ 內而非 db/：Zeabur frozen template inline dockerfile
  已 COPY scripts/，但沒 COPY db/。放 scripts/ 確保 image 內 /app/scripts/migrations/
  一定有 SQL（frozen template 升級前不要改）。

使用方式：
    # Zeabur 部署時由 backup/init container 跑一次
    POSTGRES_HOST=db POSTGRES_PORT=5432 \\
    POSTGRES_USER=supabase_admin POSTGRES_PASSWORD=... POSTGRES_DB=postgres \\
    uv run python -m scripts.apply_migrations

    # 本機跑（docker-compose）
    POSTGRES_HOST=localhost POSTGRES_USER=supabase_admin \\
    POSTGRES_PASSWORD=postgres uv run python -m scripts.apply_migrations

GoTrue auth 角色前置：
  supabase/postgres image 不會自動建立 `supabase_auth_admin` role；
  GoTrue migration runner 用這個 role 連 db 跑 `auth` schema DDL，
  所以本 runner 進 db 前先用 superuser 建好（密碼 = POSTGRES_PASSWORD）。
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import psycopg
from psycopg import sql as pg_sql

# ponytail: 放在 scripts/ 內（不是 backend/db/migrations/）— Zeabur frozen
# template inline dockerfile 已 COPY scripts/，沒 COPY db/。放這確保 image 內
# /app/scripts/migrations/ 一定有 SQL；dev / 本機同樣 layout 也 work。
MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def _ensure_gotrue_prereqs(conn: psycopg.Connection, password: str) -> None:
    """建 GoTrue migration 預設的 role + schema（self-host Zeabur 必跑）。

    supabase/postgres image 17.6.1.136 build 時不創以下兩個 role / schema，gotrue
    runtime 卻假設自帶；self-host 時這個假設破了 → migration 連環 fail：

    1. supabase_auth_admin：gotrue 用此 role 跑 auth schema migration（DawnCast 的
       0001_init.sql 也 reference auth.users FK）。CREATE ROLE 給 CREATEDB /
       CREATEROLE / REPLICATION / BYPASSRLS，密碼 = POSTGRES_PASSWORD。
    2. postgres：gotrue 後續 RLS grant 會用 `grant select on auth.users to postgres`，
       image 沒建這個 role → UndefinedObject。
    3. auth schema：gotrue 的 00_init_auth_schema.up.sql 直接 CREATE TABLE auth.users，
       schema 必須先存在。
    4. supabase_admin → supabase_auth_admin 會員：gotrue 用 supabase_auth_admin 連進
       來建表，需要 superuser 級權限（透過 membership 繼承）。

    升級路徑：若換 supabase/postgres 升級版自動建以上 role / schema，刪掉這個 pre-step。

    注意：不做 `GRANT supabase_admin TO supabase_auth_admin`。supabase/postgres image
    預設 supabase_auth_admin 已是 superuser（不需要繼承），且這條在 local dev 用普通
    postgres image（沒 supabase_admin）會壞。
    """
    print("→ 確保 GoTrue 預設 role + auth schema 存在", flush=True)

    def _ensure_role(
        cur: psycopg.Cursor,
        name: str,
        attrs: str,
        password: str,
    ) -> None:
        """role 不存在就 CREATE，存在就 ALTER 密碼。"""
        cur.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (name,))
        if cur.fetchone() is not None:
            cur.execute(
                pg_sql.SQL("ALTER ROLE {} WITH LOGIN PASSWORD {}").format(
                    pg_sql.Identifier(name), pg_sql.Literal(password)
                )
            )
        else:
            cur.execute(
                pg_sql.SQL("CREATE ROLE {} {} LOGIN PASSWORD {}").format(
                    pg_sql.Identifier(name), pg_sql.SQL(attrs), pg_sql.Literal(password)
                )
            )

    with conn.cursor() as cur:
        # 1+2. 兩個 role 都建好（密碼對齊 POSTGRES_PASSWORD；gotrue 預期如此）
        _ensure_role(
            cur,
            "supabase_auth_admin",
            "CREATEDB CREATEROLE REPLICATION BYPASSRLS",
            password,
        )
        conn.commit()
        _ensure_role(cur, "postgres", "SUPERUSER", password)
        conn.commit()

        # 3. auth schema（owner = supabase_auth_admin；image 沒預建）
        cur.execute("CREATE SCHEMA IF NOT EXISTS auth AUTHORIZATION supabase_auth_admin")
        conn.commit()

        # 4. supabase_auth_admin 預設 search_path = auth（避免每次 query 都加前綴）
        # ponytail: 不做 GRANT supabase_admin TO supabase_auth_admin — image 預設
        # supabase_auth_admin 已是 superuser，這條 GRANT 是 no-op 且在 local dev
        # 用普通 postgres image（沒 supabase_admin）會壞。
        cur.execute("ALTER ROLE supabase_auth_admin SET search_path = 'auth'")
        conn.commit()
    print("  ✓ supabase_auth_admin + postgres role + auth schema 備妥", flush=True)


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


def main(argv: list[str] | None = None) -> int:
    """跑 schema migrations。

    argv：傳給 argparse 的參數列表。
      - None（預設）→ 用 sys.argv[1:]（CLI `python -m scripts.apply_migrations` 行為）。
      - list → 顯式指定；library 呼叫端（lifespan event / worker main）傳 `[]` 走預設，
        否則會讀到 caller 的 sys.argv（例如 uvicorn 啟動參數）造成 argparse 報錯。
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只列出將要跑的檔案，不實際執行",
    )
    args = parser.parse_args(argv)

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
        # 先建 GoTrue 預設 role + auth schema（gotrue migration 前置；見函式 docstring）
        try:
            _ensure_gotrue_prereqs(conn, password)
        except Exception as exc:
            print(f"\n✗ GoTrue prereqs 確保失敗：{exc}", file=sys.stderr)
            conn.rollback()
            return 1

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
