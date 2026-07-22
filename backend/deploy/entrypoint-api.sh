#!/bin/sh
# API 容器啟動入口（Dockerfile CMD = /app/entrypoint-api.sh）：
#   1) 若 APPLY_MIGRATIONS_ON_BOOT=1 → 用 superuser（POSTGRES_USER=supabase_admin）
#      連 db 跑 scripts/apply_migrations.py，落地所有 0001~0009 SQL +
#      建 supabase_auth_admin role / auth schema（GoTrue migration 前置）。
#   2) 然後 exec uvicorn。
#
# ponytail: 此 script 是 PID 1（CMD = 它），所有 echo/print 直寫 stdout —
# docker logs / Zeabur runtime log 一定捕得到。
# 對比之前用 ENTRYPOINT + exec "$@"：Dockerfile ENTRYPOINT 跑完 exec CMD，
# Zeabur runtime log driver 沒抓 entrypoint 的 stderr，debug 不到 migration 是否跑了。
#
# 此腳本不擋缺少 superuser 的情況——若 POSTGRES_PASSWORD 未設，
# apply_migrations.py 會自行 exit 2（不啟動 uvicorn，讓 Zeabur 視為容器失敗）。

set -eu

echo "[startup-api] PID=$$ APPLY=${APPLY_MIGRATIONS_ON_BOOT:-0} PORT=${PORT:-8080}"

if [ "${APPLY_MIGRATIONS_ON_BOOT:-0}" = "1" ]; then
    echo "[startup-api] running migrations..."
    export POSTGRES_HOST="${POSTGRES_HOST:-db}"
    export POSTGRES_PORT="${POSTGRES_PORT:-5432}"
    export POSTGRES_DB="${POSTGRES_DB:-postgres}"
    # POSTGRES_USER 預期 = supabase_admin（Zeabur template 設的）
    # POSTGRES_PASSWORD 由 Zeabur Vault 注入
    python -u -m scripts.apply_migrations
    echo "[startup-api] migrations done"
fi

echo "[startup-api] launching uvicorn"
exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}