#!/bin/sh
# API 容器啟動鉤子：
#   1) 若 APPLY_MIGRATIONS_ON_BOOT=1 → 用 superuser（POSTGRES_USER=supabase_admin）
#      連 db 跑 scripts/apply_migrations.py，落地所有 0001~0009 SQL。
#      Migration runner 本身就是 idempotent，重複跑是安全的。
#   2) 然後 exec CMD（uvicorn）
#
# 此腳本不擋缺少 superuser 的情況——若 POSTGRES_PASSWORD 未設，
# apply_migrations.py 會自行 exit 2（不啟動 uvicorn，讓 Zeabur 視為容器失敗）。

set -eu

# ponytail: stderr 強制 flush，Zeabur log 查得到（解「entrypoint 跑沒 print」謎團）。
echo "[entrypoint-api] START pid=$$ APPLY=${APPLY_MIGRATIONS_ON_BOOT:-0}" >&2

if [ "${APPLY_MIGRATIONS_ON_BOOT:-0}" = "1" ]; then
    echo "[entrypoint-api] APPLY_MIGRATIONS_ON_BOOT=1，跑 migrations…" >&2
    export POSTGRES_HOST="${POSTGRES_HOST:-db}"
    export POSTGRES_PORT="${POSTGRES_PORT:-5432}"
    export POSTGRES_DB="${POSTGRES_DB:-postgres}"
    # POSTGRES_USER 預期 = supabase_admin（Zeabur template 設的）
    # POSTGRES_PASSWORD 由 Zeabur Vault 注入
    python -u -m scripts.apply_migrations
    echo "[entrypoint-api] migrations done" >&2
fi

exec "$@"
