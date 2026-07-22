#!/bin/sh
# Worker 容器啟動入口（Dockerfile CMD = /app/entrypoint-worker.sh）：
#   1) 若 APPLY_MIGRATIONS_ON_BOOT=1 → 跑 migrations（同 api 邏輯；9 個 SQL 都用
#      if not exists / create or replace / 等冪等模式，重跑不會壞）。
#   2) 然後 exec worker poll。
#
# ponytail: 同 entrypoint-api，此 script 是 PID 1，所有 stdout 一定進 Zeabur log。

set -eu

echo "[startup-worker] PID=$$ APPLY=${APPLY_MIGRATIONS_ON_BOOT:-0}"

if [ "${APPLY_MIGRATIONS_ON_BOOT:-0}" = "1" ]; then
    echo "[startup-worker] running migrations..."
    export POSTGRES_HOST="${POSTGRES_HOST:-db}"
    export POSTGRES_PORT="${POSTGRES_PORT:-5432}"
    export POSTGRES_DB="${POSTGRES_DB:-postgres}"
    python -u -m scripts.apply_migrations
    echo "[startup-worker] migrations done"
fi

echo "[startup-worker] launching worker"
exec python -m engine.worker