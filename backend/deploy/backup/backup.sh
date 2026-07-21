#!/bin/sh
# pg_dump → gzip → R2。失敗不刪本地檔，丟 stderr，cron 隔天會再試一次。
#
# 用 R2 endpoint 形式（aws-cli 通用）：s3 兼容協議。

set -eu

if [ -z "${POSTGRES_HOST:-}" ] || [ -z "${POSTGRES_PASSWORD:-}" ] || \
   [ -z "${R2_ACCOUNT_ID:-}" ] || [ -z "${R2_ACCESS_KEY_ID:-}" ] || \
   [ -z "${R2_SECRET_ACCESS_KEY:-}" ] || [ -z "${R2_BACKUP_BUCKET:-}" ]; then
  echo "$(date -Iseconds) backup.sh: missing one of POSTGRES_*/R2_* env" >&2
  exit 1
fi

TS=$(date -u +"%Y-%m-%dT%H%M%SZ")
FILE="/tmp/dawncast-${TS}.sql.gz"

# dump
PGPASSWORD="$POSTGRES_PASSWORD" pg_dump \
  --no-owner --no-privileges --clean --if-exists \
  -h "$POSTGRES_HOST" \
  -p "${POSTGRES_PORT:-5432}" \
  -U "${POSTGRES_USER:-postgres}" \
  -d "${POSTGRES_DB:-postgres}" \
  | gzip -9 > "$FILE"

# 傳 R2
AWS_ACCESS_KEY_ID="$R2_ACCESS_KEY_ID" \
AWS_SECRET_ACCESS_KEY="$R2_SECRET_ACCESS_KEY" \
aws s3 cp "$FILE" \
  "s3://${R2_BACKUP_BUCKET}/postgres/$(basename "$FILE")" \
  --endpoint-url "https://${R2_ACCOUNT_ID}.r2.cloudflarestorage.com" \
  --only-show-errors

# 清本地檔
rm -f "$FILE"

# R2 端 retention 不靠 Object Lock（free tier 沒），改用 lifecycle rule 在
# Zeabur 或 R2 dashboard 設定「超過 30 天自動刪除 postgres/ prefix」。
echo "$(date -Iseconds) backup ok: s3://${R2_BACKUP_BUCKET}/postgres/dawncast-${TS}.sql.gz"
