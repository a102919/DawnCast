#!/usr/bin/env bash
# DawnCast R2 bucket 一次性建置（單人精簡版）。
#
# 用 Cloudflare API v4 建兩個 bucket：
#   1. ${MAIN_BUCKET:-dawncast}          — 音檔 / 字幕
#   2. ${BACKUP_BUCKET:-dawncast-backups}— 每日 pg_dump
# 然後幫 backups bucket 加 lifecycle rule：30 天自動刪。
#
# 必設 env（先 read -p / source .env / 直接 export 都可）：
#   R2_ACCOUNT_ID          Cloudflare account id（不是 token 本身）
#   R2_TOKEN               API token，權限含 Workers R2 Storage: Edit +
#                          Account Settings: Read（見 deploy/README.md §2.2）
#
# 用：
#   R2_ACCOUNT_ID=zzzz R2_TOKEN=cfut_xxx bash backend/deploy/scripts/setup_r2.sh

set -euo pipefail

if [[ -z "${R2_ACCOUNT_ID:-}" || -z "${R2_TOKEN:-}" ]]; then
  echo "缺 R2_ACCOUNT_ID 或 R2_TOKEN" >&2
  exit 2
fi

ACCT="$R2_ACCOUNT_ID"
TOKEN="$R2_TOKEN"
MAIN="${MAIN_BUCKET:-dawncast}"
BACKUP="${BACKUP_BUCKET:-dawncast-backups}"

api() {
  curl -fsS -X "$1" "$2" \
    -H "Authorization: Bearer ${TOKEN}" \
    -H "Content-Type: application/json" \
    ${3:+--data-binary @<(printf '%s' "$3")}
}

# ━━━━━ 0) 偷看現有 bucket 列表 ━━━━━
echo "→ 列出已有 bucket..."
LIST=$(curl -fsS -X GET \
  "https://api.cloudflare.com/client/v4/accounts/${ACCT}/r2/buckets" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json")
echo "$LIST" | python3 -m json.tool 2>/dev/null || echo "$LIST"
echo

# ━━━━━ 1) 建 dawncast（idempotent：已存在時 400 但整段腳本繼續）━━━━━
create_bucket() {
  local NAME="$1"
  echo "→ 建立 bucket: ${NAME}"
  local BODY=$(cat <<EOF
{"name":"${NAME}","locationHint":"apac","storageClass":"Standard"}
EOF
)
  HTTP=$(curl -sS -o /tmp/r2-create.json -w "%{http_code}" -X PUT \
    "https://api.cloudflare.com/client/v4/accounts/${ACCT}/r2/buckets/${NAME}" \
    -H "Authorization: Bearer ${TOKEN}" \
    -H "Content-Type: application/json" \
    --data-binary "$BODY")
  if [[ "$HTTP" == "200" || "$HTTP" == "201" ]]; then
    echo "  ✓ 已建立 / 已存在"
  elif [[ "$HTTP" == "400" ]]; then
    # 已存在也算 ok；Cloudflare PUT on existing bucket 會回 400「name taken」之類
    echo "  ⚠ bucket ${NAME} 已存在（忽略）："
    cat /tmp/r2-create.json | python3 -m json.tool 2>/dev/null || cat /tmp/r2-create.json
  else
    echo "  ✗ HTTP ${HTTP}："; cat /tmp/r2-create.json; return 1
  fi
  echo
}

create_bucket "$MAIN"
create_bucket "$BACKUP"

# ━━━━━ 2) backup bucket 加 lifecycle：30 天刪 ━━━━━
echo "→ 設定 backups bucket lifecycle（30 天自動刪 postgres/ prefix）..."
LC_BODY=$(cat <<'EOF'
{
  "rules": [
    {
      "id": "expire-backups-30d",
      "enabled": true,
      "prefix": "postgres/",
      "conditions": { "type": "Age", "maxAge": 30 },
      "action": "Delete"
    }
  ]
}
EOF
)
HTTP=$(curl -sS -o /tmp/r2-lifecycle.json -w "%{http_code}" -X PUT \
  "https://api.cloudflare.com/client/v4/accounts/${ACCT}/r2/buckets/${BACKUP}/lifecycle" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  --data-binary "$LC_BODY")
if [[ "$HTTP" == "200" || "$HTTP" == "201" ]]; then
  echo "  ✓ lifecycle 已套用"
  cat /tmp/r2-lifecycle.json | python3 -m json.tool 2>/dev/null || cat /tmp/r2-lifecycle.json
elif [[ "$HTTP" == "400" ]]; then
  # Cloudflare R2 lifecycle 目前不支援 prefix-only delete 也不能整桶 maxAge，
  # 而且常常 PUT lifecycle 對小帳號是回「不支援」而非 200。
  # 不致命，只警告。
  echo "  ⚠ lifecycle 設失敗（HTTP ${HTTP}）；R2 free tier 可能限制。"
  cat /tmp/r2-lifecycle.json | python3 -m json.tool 2>/dev/null || cat /tmp/r2-lifecycle.json
  echo "    手動備援：去 R2 Dashboard → ${BACKUP} → Object Lifecycle → 設 30 天刪"
else
  echo "  ✗ HTTP ${HTTP}："; cat /tmp/r2-lifecycle.json
fi
echo

# ━━━━━ 3) 驗收：列出 bucket ━━━━━
echo "→ 驗收最終列表..."
curl -fsS -X GET \
  "https://api.cloudflare.com/client/v4/accounts/${ACCT}/r2/buckets" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  | python3 -m json.tool

echo
echo "✓ 全部完成。可填進 backend/.env："
echo "    R2_ACCOUNT_ID=${ACCT}"
echo "    R2_BUCKET=${MAIN}"
echo "    R2_BACKUP_BUCKET=${BACKUP}"
echo "    R2_ENDPOINT=https://${ACCT}.r2.cloudflarestorage.com"
echo
echo "Access Key / Secret 從 Cloudflare Dashboard → R2 → Manage R2 API Tokens 取（非這個 token）。"
