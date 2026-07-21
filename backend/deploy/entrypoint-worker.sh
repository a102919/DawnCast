#!/bin/sh
# Worker 容器啟動鉤子：
#   同 entrypoint-api，跑 migrations（同樣條件）後再啟 worker poll。
#
# api 跟 worker 都會嘗試跑 migrations，但 9 個 SQL 都用 if not exists /
# create or replace / 等冪等模式，重跑不會壞。

set -eu

if [ "${APPLY_MIGRATIONS_ON_BOOT:-0}" = "1" ]; then
    echo "[entrypoint-worker] APPLY_MIGRATIONS_ON_BOOT=1，跑 migrations…"
    export POSTGRES_HOST="${POSTGRES_HOST:-db}"
    export POSTGRES_PORT="${POSTGRES_PORT:-5432}"
    export POSTGRES_DB="${POSTGRES_DB:-postgres}"
    python -m scripts.apply_migrations
fi

exec "$@"
