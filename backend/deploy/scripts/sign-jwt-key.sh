#!/usr/bin/env bash
# 產出 DawnCast Self-host GoTrue 用的 JWT signing key pair。
#
# GoTrue 預設走 ES256（ECC P-256），Supabase Cloud 在 2025 年強制換過，
# DawnCast backend/app/deps.py 用 JWKS endpoint 驗 ES256 token，所以
# 自架必須用同一種簽章，否則前端登入會全部 401。
#
# 用法：
#   ./sign-jwt-key.sh                  → 產出 key pair 到 ./jwt-keys/
#   ./sign-jwt-key.sh /path/to/dir     → 指定輸出目錄
#
# 輸出兩個檔案：
#   <dir>/jwt_es256.pem        ← ECC PRIVATE KEY（PEM）；Zeabur env GOTRUE_JWT_KEY 直接貼
#   <dir>/jwt_es256.pub        ← ECC PUBLIC KEY（PEM）；備用（GoTrue 會自動從 private 推導）
#
# 注意：private key 屬最高機密，請存進 Zeabur Secrets 區、不要 commit。

set -euo pipefail

OUT_DIR="${1:-./jwt-keys}"
mkdir -p "$OUT_DIR"
chmod 700 "$OUT_DIR"

PRIV="$OUT_DIR/jwt_es256.pem"
PUB="$OUT_DIR/jwt_es256.pub"

if [[ -f "$PRIV" ]]; then
  echo "⚠️  $PRIV 已存在，不覆寫（先 rm 再重跑）。" >&2
  exit 1
fi

# 產 ECC P-256（prime256v1 = secp256r1）private key
openssl ecparam -name prime256v1 -genkey -noout -out "$PRIV"
chmod 600 "$PRIV"

# 推 public（PEM，GoTrue 內部不需要；留著方便 rotate / debug）
openssl ec -in "$PRIV" -pubout -out "$PUB"
chmod 644 "$PUB"

echo ""
echo "✓ key pair 已產出："
echo "  private: $PRIV  ← 貼到 Zeabur GOTRUE_JWT_KEY（整段 PEM 含 BEGIN/END 行）"
echo "  public : $PUB  ← 備用；JWKS 由 GoTrue 自動從 private 推導"
echo ""
echo "旋轉流程（rotate）："
echo "  1. 產新 key pair（mv 舊檔到 $OUT_DIR/archive-YYYYMMDD/）"
echo "  2. 把新 key 寫到 GOTRUE_JWT_KEY（**保留**舊 key 在同檔最下面；"
echo "     GoTrue 會自動 rotate-sign，新 token 用新 key 簽，驗時兩個都收）"
echo "  3. 等 24-48 小時（讓所有 client refresh 拿到新 kid 簽的 token）"
echo "  4. 把舊 key 從 GOTRUE_JWT_KEY 移除"
echo "  ⚠ 0 down time，**不需要**重啟 FastAPI（kid rotation by JWKS）"
