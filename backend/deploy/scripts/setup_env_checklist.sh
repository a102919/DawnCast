#!/usr/bin/env bash
# DawnCast 部署 Zeabur env 對照清單（單人精簡版）
# 直接 copy → Zeabur Dashboard → Service → Variables → 貼上。
#
# ⚠️ 警告：本檔不內含 secret —— 只列出哪個 service 要放什麼 env 變數。
#         Secret 由你自己在 Zeabur Dashboard 一個一個貼入。

set -e
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  DawnCast 部署 env 對照表（單人精簡版）"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo
cat <<'EOF'

# ━━━━━━━ 你要先備好這些（自己準備、不放 git、paste 到 Zeabur）━━━━━━━━

# 必設（缺一就 fail）：
POSTGRES_PASSWORD=<你決定的強密碼，例 openssl rand -hex 32>
ADMIN_TOKEN=<強祕密，同上產生方式>
MINIMAX_API_KEY=<已申請的 MiniMax auth token>

# Gotrue / Auth 必設（用 deploy/scripts/sign-jwt-key.sh 產）：
GOTRUE_JWT_KEY=<整段 PEM 含 BEGIN/END>

# Google OAuth（從 console.cloud.google.com 拿）：
GOOGLE_CLIENT_ID=xxx.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=GOCSPX-xxxx

# R2（從 Cloudflare Dashboard 拿）：
R2_ACCOUNT_ID=
R2_ACCESS_KEY_ID=
R2_SECRET_ACCESS_KEY=
R2_ENDPOINT=https://<account>.r2.cloudflarestorage.com
R2_BUCKET=dawncast

# 公開網域（之後 Zeabur 對外域名定下來後回填）：
SITE_URL=https://<your-domain>
API_EXTERNAL_URL=https://<api-subdomain>.<your-domain>

# GoTrue OAuth URI 白名單（JSON 字串）：
URI_ALLOW_LIST=["https://<your-domain>","https://<api-subdomain>.<your-domain>/auth/v1/callback"]

# ━━━━━━━ 哪個 service 設什麼 env ━━━━━━━

# db (supabase/postgres:17.6.1.136)：
#   POSTGRES_PASSWORD=        ← 同上面的值
#   （無其他；image 預設 supabase_admin / postgres / TZ Asia/Taipei 都內建）

# gotrue (supabase/gotrue:v2.189.0)：
#   POSTGRES_PASSWORD
#   GOTRUE_JWT_KEY
#   GOOGLE_CLIENT_ID
#   GOOGLE_CLIENT_SECRET
#   SITE_URL
#   API_EXTERNAL_URL
#   URI_ALLOW_LIST

# api (Dockerfile.api)：
#   POSTGRES_PASSWORD
#   MINIMAX_API_KEY
#   API_BASE_URL=https://api.minimax.io/anthropic
#   API_MODEL=MiniMax-M3
#   R2_ACCOUNT_ID
#   R2_ACCESS_KEY_ID
#   R2_SECRET_ACCESS_KEY
#   R2_BUCKET
#   R2_ENDPOINT
#   SITE_URL
#   API_EXTERNAL_URL
#   CORS_ALLOWED_ORIGINS=["https://<your-domain>"]
#   ADMIN_TOKEN
#   TAVILY_API_KEY=          ← 缺可空，自動降級
#   APPLY_MIGRATIONS_ON_BOOT=1
#   POSTGRES_USER=supabase_admin
#   POSTGRES_HOST=db
#   POSTGRES_PORT=5432
#   POSTGRES_DB=postgres

# worker (Dockerfile.worker)：
#   跟 api 一樣，但少了：
#     CORS_ALLOWED_ORIGINS / SITE_URL / API_EXTERNAL_URL

EOF
echo "✓ 對照表已產出（見上方）"
