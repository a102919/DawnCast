# 部署（DawnCast Self-host on Zeabur — 單人精簡版）

單一 host 跑四個容器：**api + worker + db + gotrue**。
對外只露 FastAPI `:8080`；HTTPS + JWKS 由 Cloudflare Tunnel 接手；備份改成手動 pg_dump 腳本。

---

## 0. 規格 / 月費估算

| 項 | 規格 |
|---|---|
| CPU | 2 vCPU |
| RAM | 4 GB（4 GB 舒適；2 GB 緊、可能 swap thrash） |
| SSD | 40-60 GB（DB + 字幕；音檔走 R2 不在 host） |
| 流量 | 0.5-1.5 TB/月（host 對外僅 JWKS 跟 API 呼叫；單人用 < 5 GB/月） |
| Region | **TenCent Tokyo $4/mo**（亞太樞紐、對 MiniMax API 最短） |
| **合計** | **$4/mo**（不含 LLM / R2） |

月費合計範例：

| 項 | $ |
|---|---|
| Zeabur Server Tokyo 4GB | $4 |
| Cloudflare Pages（前端） | $0 |
| Cloudflare Tunnel（內網穿透） | $0 |
| Cloudflare R2（音檔 + 字幕備份） | ~$0.5 |
| MiniMax API（單人用） | ~$1-3/月（看用量） |
| **合計** | **~$5-7/mo** |

---

## 1. 拓樸

```
┌─ Zeabur Server (TenCent Tokyo, 4GB) ───────────────────────────┐
│                                                                 │
│  api (Dockerfile.api)         → FastAPI :8080                  │
│     ├── /api/* 與 /auth/v1/* → FastAPI 處理（內部 reverse      │
│     │      proxy 到 gotrue:9999 處理 /auth/*）                  │
│     └── /health (Zeabur healthcheck)                           │
│                                                                 │
│  worker (Dockerfile.worker)   → python -m engine.worker        │
│     輪詢 pgmq 三條 queue：control / generate / dict_translate    │
│                                                                 │
│  db (supabase/postgres:17)    → :5432 內網（不開 port）         │
│     ├── pgmq    (control / generate / dict_translate)            │
│     ├── pg_cron (evergreen fallback 03:30 Taipei)               │
│     ├── pgvector (episode embeddings)                           │
│     └── 內含 auth.users / auth.refresh_tokens / handle_new_user│
│                                                                 │
│  gotrue (supabase/gotrue)     → :9999 (內網)                    │
│     └── Google OAuth 唯一登入 + ES256 簽 JWT + JWKS endpoint   │
└─────────────────────────────────────────────────────────────────┘
   ↑ :8080 HTTPS (對外)
   │
Cloudflare Tunnel (cloudflared daemon)
   ↓ 對應 Cloudflare zone 跟 DNS
   ↓
┌────────────────────────────────────────────────────────────────┐
│  Browser ↔ Cloudflare Pages (前端) ↔ Cloudflare Tunnel (HTTPS) │
│                              ↘ FastAPI :8080                   │
└────────────────────────────────────────────────────────────────┘

外部服務：
  • Cloudflare Pages       → 前端
  • Cloudflare R2          → 音檔/字幕
  • Cloudflare Tunnel      → 對內網穿透（無 egress 費）
  • MiniMax API             → 生成引擎（HTTP 對外）
  • Google OAuth            → 唯一登入
```

---

## 2. 首次部署（從零到跑）

### 2.1 採購 Zeabur Server
1. Zeabur Dashboard → Marketplace → Server → 選 **TenCent / Tokyo / 4 vCPU / 4 GB / 60 GB SSD**（$4/mo 那一列）
2. 拿到後 Dashboard 新增 Project `dawncast-personal`

### 2.2 採購雲端依賴
1. **Cloudflare zone**（要 DNS 託管的網域）
   - Workers → Tunnel → `dawncast-api-tunnel` → 複製 token
2. **Cloudflare Pages** → 連 GitHub → 選 repo
   - Build command：`npm run build`
   - Build output：`dist`
   - Root directory：`frontend`
   - 一個 env 都不先設，等等跟 Tunnel 一起設定（避免被推進 build）
3. **Cloudflare R2** → 產 API Token（Access Key + Secret + Account ID）
4. **Google Cloud Console** → API & Services → Credentials
   - 建 OAuth client → Authorized redirect URI
     - 本機 dev：`http://localhost:8080/auth/v1/callback`
     - prod：`https://<API_EXTERNAL_URL>/auth/v1/callback`

### 2.3 產 JWT signing key
```bash
cd backend/deploy/scripts
./sign-jwt-key.sh ./jwt-keys
# 將 jwt-keys/jwt_es256.pem 整段（含 BEGIN/END）複製下來
```
**安全**：私鑰**不 commit**。

### 2.4 部署到 Zeabur
```bash
cd backend
zeabur deploy --template deploy/zeabur-template.yaml
```

接著進 Dashboard → 每個 service 設 env：

| Env | 設定處 | 必設 | 說明 |
|---|---|---|---|
| `POSTGRES_PASSWORD` | db, gotrue, api, worker | ✅ | 同值（強密碼） |
| `GOTRUE_JWT_KEY` | gotrue | ✅ | 2.3 整段 PEM |
| `GOOGLE_CLIENT_ID` | gotrue | ✅ | |
| `GOOGLE_CLIENT_SECRET` | gotrue | ✅ | |
| `SITE_URL` | gotrue | ✅ | 前端公開網域 |
| `API_EXTERNAL_URL` | gotrue, api | ✅ | Tunnel 對外網域 |
| `URI_ALLOW_LIST` | gotrue | ✅ | JSON 陣列字串 |
| `CORS_ALLOWED_ORIGINS` | api | ✅ | JSON 陣列字串 |
| `ADMIN_TOKEN` | api, worker | ✅ | 強祕密 |
| `MINIMAX_API_KEY` | api, worker | ✅ | 你的 LLM token |
| `API_BASE_URL` | api, worker | ✅ | `https://api.minimax.io/anthropic` 之類 |
| `API_MODEL` | api, worker | ✅ | `MiniMax-M2.5` 之類 |
| `R2_*` | api, worker | ✅ | 帳號 / key / secret / bucket / endpoint |
| `TAVILY_API_KEY` | api | ⚪ | 缺則自動降級 |
| `APPLY_MIGRATIONS_ON_BOOT` | api, worker | ⚪ | 首次 deploy 設 `1`、之後改 `0`（個人用就 `1` 也行；9 支 SQL 冪等） |
| `POSTGRES_USER` | api, worker | ⚪ | `supabase_admin` 才能跑 migrations |
| `POSTGRES_HOST` | api, worker | ⚪ | `db` |
| `POSTGRES_PORT` | api, worker | ⚪ | `5432` |
| `POSTGRES_DB` | api, worker | ⚪ | `postgres` |
| `R2_BUCKET` | api, worker | ⚪ | 預設 `dawncast`，可不設 |

### 2.5 設 Cloudflare Tunnel
1. Workers → Tunnel → 重新 Create → 選 token 貼上 deploy 指令
2. 加 Public hostname：
   - 主機名 `<API_EXTERNAL_URL>` → Service `http://zeabur-api-host:8080`
     - Zeabur 內部 host 透過 `container-name.zeabur.internal` 訪問（Zeabur 同 Project 內）
     - 或填 Zeabur Project 的 private domain（Deploy 後 Dashboard → Project → Networking）
3. 加完之後：
   - 設 Cloudflare DNS：`<API_EXTERNAL_URL>` CNAME 自動生成（Cloudflare 接管 zone）
   - DNS-only（灰色雲）→ 不能用，因為 Tunnel 必須 proxied
4. Cloudflare SSL：Full（Strict）— Zeabur 自簽 / Cloudflare 通用憑證

### 2.6 驗證
```bash
# 1. FastAPI health
curl https://<API_EXTERNAL_URL>/health
# → {"status":"ok"}

# 2. JWKS
curl https://<API_EXTERNAL_URL>/auth/v1/.well-known/jwks.json
# → 公開 JWKS JSON（ES256 key）

# 3. 進 db container（Zeabur Dashboard → db service → Shell）
psql -U postgres -d postgres
> \dt
# 期望見：users, episodes, deliveries, user_vocab, dict_cache 等 9+ 表
```

### 2.7 備援部署（單人版特殊）

#### 手動 pg_dump（個人用；資料掉了就失去語料庫，重灌 migration 不再）
```bash
# 在 Zeabur Dashboard → db container → Shell 跑：
pg_dump -U postgres -d postgres | gzip > /tmp/backup-$(date +%Y%m%d).sql.gz

# 或在本機（db container port 設為 127.0.0.1:54322）：
PGPASSWORD=<POSTGRES_PASSWORD> pg_dump -h 127.0.0.1 -p 54322 -U postgres postgres | gzip > backup.sql.gz
```

#### 一次性 R2 sync（音檔）
```bash
# 用 rclone（一次性；不架 sidecar）
rclone sync r2:dawncast /tmp/r2-dump --transfers=8 --checkers=16
```

---

## 3. 後續部署流程

`git push origin` 之後 deploy：

```bash
cd backend
zeabur deploy --template deploy/zeabur-template.yaml   # api / worker 重 build
# db / gotrue 不動（image 沒變）
```

之後有需要再開 GitHub Actions；目前不啟動。

---

## 4. SOP：JWT Signing Key Rotate

洩漏 / 預期過期 / 年度強制 rotate：

```bash
# 1. 產新 key pair
cd backend/deploy/scripts
./sign-jwt-key.sh /tmp/rotate-keys

# 2. Zeabur Dashboard → gotrue env → GOTRUE_JWT_KEY
#    格式（**保留舊 key 在下面**；GoTrue 多 key 並存）：
#       -----BEGIN EC PRIVATE KEY-----
#       <新私鑰內容>
#       -----END EC PRIVATE KEY-----
#       -----BEGIN EC PRIVATE KEY-----
#       <舊私鑰內容>
#       -----END EC PRIVATE KEY-----

# 3. 等 24-48 小時（用戶 refresh token 換到新 kid 簽）

# 4. 從 GOTRUE_JWT_KEY 移除舊 key

# 5. 0 down time。**不需要**重啟 FastAPI（JWKS 抓 ES256 公鑰在驗證時才 fetch）

# 6. 清本地
shred -u /tmp/rotate-keys/jwt_es256.pem
```

---

## 5. SOP：Supabase 月度升級

[supabase/supabase releases](https://github.com/supabase/supabase/releases) 約每月出新版本。

```bash
# 1. 訂閱 RSS 或 gh release list --repo supabase/supabase --limit 5

# 2. 升 minor（例 2.189 → 2.190）：
#    zeabur-template.yaml 的 gotrue image tag 改新版
#    → 走 staging 演練
#    → release notes 看是否要 ALTER ROLE / schema migration 跑手動

# 3. 升 Postgres major（17 → 18，要等幾年）：
#    走 supabase 官方 Upgrade 流程
#    順序：dump → 起新 instance → restore → 切 DNS → 下線舊

# 4. 升級順序：db → gotrue → api → worker
#    早 deploy 的服務是後面的依賴，不顛倒

# 5. Zeabur Dashboard → Redeploy project → 觀察 healthcheck
```

---

## 6. 本機開發

```bash
cd backend
cp .env.example .env
# 編輯 .env：
#   POSTGRES_PASSWORD=<本機強密碼>
#   GOTRUE_JWT_KEY="$(cat deploy/scripts/jwt-keys/jwt_es256.pem)"
#   GOOGLE_CLIENT_ID=... GOOGLE_CLIENT_SECRET=...
#   MINIMAX_API_KEY=...
#   R2_* 用 dev bucket 的 key

docker compose up -d          # 起 4 個容器 + 自動跑 migrations（首啟 1 次）
docker compose run --rm migrate   # 手動跑（一次性）
docker compose down          # 停（volume 留著）
```

- 埠號：`db:54322`（本機 psql 用）、`gotrue:9999`、`api:8080`
- 前端 dev：`cd frontend && npm run dev` → vite 5173 跑 → 透過 `vite.config.ts` proxy 打 `localhost:8080`

---

## 7. 故障排除

| 症狀 | 看哪裡 | 解法 |
|---|---|---|
| Google 登入 callback 400 | Zeabur logs → gotrue | `URI_ALLOW_LIST` 含 `<API_EXTERNAL_URL>/auth/v1/callback`？ |
| 前端拿不到 JWT | 瀏覽器 devtools | `curl https://<API_EXTERNAL_URL>/auth/v1/.well-known/jwks.json` 有回嗎？ |
| JWKS 404 | Zeabur logs → api | FastAPI `/auth/v1/*` reverse proxy 是否設定（見 app/main.py 規畫） |
| db 沒表 | Zeabur logs → api | 看 entrypoint-api.sh 有沒有跑 migrations？`APPLY_MIGRATIONS_ON_BOOT=1`？ |
| worker poll 不到任務 | Zeabur logs → worker | db 健康？`POSTGRES_PASSWORD` 設了嗎？ |
| pg_cron 沒跑 | psql → `select * from cron.job_run_details order by start_time desc limit 10;` | DB 時區：`alter database postgres set timezone = 'Asia/Taipei';` |
| ffmpeg 燒字幕變方框 | Zeabur logs → worker | Dockerfile.worker 沒漏裝 fonts-noto-cjk 吧？image 重 build |

---

## 8. 已知限制 / 注意事項

| 項目 | 說明 |
|---|---|
| **單點故障** | Host 死 = 全部 service 死。Mitigation：手動 pg_dump（§2.7）。 |
| **無 HA** | 單 master。要 HA 上 multi-host；個人版不做。 |
| **每件事都沒有監控** | 個人版不要裝 Sentry / Better Stack。出問題翻 Zeabur logs 就夠。 |
| **不裝 Studio** | 省 500MB RAM。需要 GUI 排查用 `docker exec -it db psql`。 |
| **共用 Cookie / JWKS** | JWT 升級時舊 key 保留 24-48h；§4 強制縮短回收期。 |
| **Server 重灌會丟 env / volume** | Zeabur Dashboard 的 secret 跟 volume 在重灌後都沒了；本來就 1 個月一次備份的人影響不大。 |

---

## 9. 環境變數全對照表（參照）

完整 env 含 default / 敏感性見 `backend/.env.example` 跟 `backend/shared/config.py:Settings`。

**必要 prod env**：
```
ENVIRONMENT=prod
DATABASE_URL=postgres://postgres:<POSTGRES_PASSWORD>@db:5432/postgres
SUPABASE_JWKS_URL=https://<API_EXTERNAL_URL>/auth/v1/.well-known/jwks.json
SUPABASE_JWT_AUDIENCE=authenticated
CORS_ALLOWED_ORIGINS=["https://<frontend-domain>"]
CORS_ALLOWED_ORIGIN_REGEX=""
ADMIN_TOKEN=<強祕密>

# Worker 額外
APP_TIMEZONE=Asia/Taipei
GENERATION_ENGINE=api_key
FAILOVER_MODE=degrade
API_KEY=<MINIMAX_API_KEY>
API_BASE_URL=https://api.minimax.io/anthropic
API_MODEL=MiniMax-M2.5

# R2
R2_ACCOUNT_ID=<CLOUDFLARE_ACCOUNT_ID>
R2_ACCESS_KEY_ID=<R2 KEY>
R2_SECRET_ACCESS_KEY=<R2 SECRET>
R2_BUCKET=dawncast
R2_ENDPOINT=https://<account>.r2.cloudflarestorage.com

# 選填（缺則自動降級）
TAVILY_API_KEY=<TAVILY KEY>

# entrypoint 跑 migrations 用（單人用預設 1）
APPLY_MIGRATIONS_ON_BOOT=1
POSTGRES_USER=supabase_admin
POSTGRES_HOST=db
POSTGRES_PORT=5432
POSTGRES_DB=postgres
```

**dev only**：`DEV_AUTH_BYPASS=true` / `DEV_USER_ID=<uuid>`——production 自動 fail。
