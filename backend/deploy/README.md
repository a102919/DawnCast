# 部署（DawnCast Self-host on Zeabur Marketplace — 單人精簡版）

單一 Zeabur Project 跑 4 個 marketplace service：**db + gotrue + api + worker**；
前端 SPA 丟 **Cloudflare Pages**。**不買網域、不架 Tunnel、不裝監控**——免費子網域
（`*.zeabur.app` + `*.pages.dev`）就夠單人用。

---

## 0. 月費估算

| 項 | $ |
|---|---|
| Zeabur marketplace 4 services（postgres + gotrue + api + worker） | ~$5 |
| Cloudflare Pages（前端） | $0 |
| Cloudflare R2（音檔 / 字幕） | ~$0.5 |
| MiniMax API（生成引擎） | ~$1-3/月 |
| **合計** | **~$6-9/月** |

---

## 1. 架構總覽

```
┌─ Zeabur Project: dawncast-personal (marketplace) ─────────────────┐
│                                                                   │
│  api-ovate  (GitHub source, Dockerfile.api)  FastAPI :8080        │
│     ├── /vocab /settings /favorites /daily-orders /episodes /dict │
│     ├── /auth/v1/*  →  reverse proxy  →  gotrue-mon:9999/{path}   │
│     └── /health                                                    │
│                                                                   │
│  worker-gir  (GitHub source, Dockerfile.worker)                    │
│     python -m engine.worker  輪詢 pgmq: control / generate / dict │
│                                                                   │
│  gotrue-mon  (marketplace supabase/gotrue:v2.189.0)  :9999        │
│     Google OAuth 唯一登入 + HS256 sign JWT                        │
│                                                                   │
│  db-pran  (marketplace supabase/postgres:17.6.1.136)  :5432 內網  │
│     pgmq + pgvector + auth.users (gotrue 管) + public schema       │
└───────────────────────────────────────────────────────────────────┘
   ↑ Zeabur edge HTTPS（對外 *.zeabur.app）

┌───────────────────────────────────────────────────────────────────┐
│  Browser  ↔  Cloudflare Pages（前端 SPA，dawncast.pages.dev）     │
│         ↘  api-ovate.zeabur.app  (FastAPI + reverse proxy)         │
└───────────────────────────────────────────────────────────────────┘

外部服務:
  • Cloudflare R2   → 音檔 / 字幕 (S3 相容 API)
  • MiniMax API     → LLM 生成引擎
  • Google OAuth    → 唯一登入入口
```

---

## 2. 服務與 URLs

| Service | Image / Source | Internal Host | External URL | 用途 |
|---|---|---|---|---|
| `db-pran` | `supabase/postgres:17.6.1.136`（marketplace） | `db-pran.zeabur.internal:5432` | — | pgmq / pgvector / auth schema / public schema |
| `gotrue-mon` | `supabase/gotrue:v2.189.0`（marketplace） | `service-...:9999` | `https://gotrue-mon.zeabur.app` | Google OAuth + HS256 sign JWT |
| `api-ovate` | `Dockerfile.api`（GitHub source） | `service-...:8080` | `https://api-ovate.zeabur.app` | FastAPI + `/auth/v1/*` reverse proxy |
| `worker-gir` | `Dockerfile.worker`（GitHub source） | `service-...`（無對外） | — | 寫稿 + TTS + ffmpeg 燒字幕 |
| Cloudflare Pages | `frontend/`（GitHub source） | — | `https://dawncast.pages.dev` | SPA |

service ID 是 Zeabur 內部識別碼，不寫死——deploy 時 Dashboard 看；prod runtime 從
`GOTRUE_MON_HOST` / `DB_PRAN_HOST` / `WORKER_GIR_HOST` / `API_OVATE_HOST` env 拿。

---

## 3. 首次部署（從零到跑）

順序很重要——後面的服務依賴前面。完成所有 env 設好之後需重啟 container 才生效。

### 3.1 4 個 Zeabur service（依序）

1. **`db-pran`**（marketplace Postgres）：Add Service → Marketplace → Postgres。
   記下 internal hostname。進 db shell 跑 prereqs SQL（修 `search_path` + drop
   partial migrations，**詳見 memory `zeabur-deploy-resources.md` 的「Zeabur
   marketplace 模式踩坑集」段**，SQL 在那段，**不要每次重打**——一次性的）。
2. **`gotrue-mon`**（marketplace GoTrue）：Add Service → Marketplace → GoTrue
   （Zeabur 自動選 `v2.189.0`）。設 env（§7.2）；首次啟動會自動跑 69 條 gotrue
   migration 建 `auth.*`。
3. **`api-ovate`**（GitHub source）：Add Service → GitHub → 選
   `a102919/DawnCast`。Zeabur build `backend/deploy/Dockerfile.api`。
   `SUPABASE_JWT_ALG=HS256`、`SUPABASE_JWT_SECRET` 跟 `GOTRUE_JWT_SECRET` 同值。
   `CORS_ALLOWED_ORIGINS` 暫留空，等 §3.3 Pages 建好再加。
4. **`worker-gir`**（GitHub source）：類似上一步，build `Dockerfile.worker`
   （裝 ffmpeg + fonts-noto-cjk，燒字幕用）。

### 3.2 Cloudflare Pages

1. Cloudflare Dashboard → **Workers & Pages** → Create application → **Pages**
   → Connect to Git → 選 `a102919/DawnCast`
2. Project name 隨意（URL = `<name>.pages.dev`），範例用 `dawncast`
3. Build settings：Framework = None、command = `npm run build`、output = `dist`、
   **Root directory = `frontend`** ← 漏這個會「找不到 package.json」build 失敗
4. 第一次 deploy 故意不設 env（先確定 build 跑通）

### 3.3 同步 env + Google OAuth

Pages 拿到 URL 後雙向同步：

| 對象 | 設什麼 |
|---|---|
| Pages env（Dashboard → Settings → Environment variables） | §7.3 四個 `VITE_*` |
| api-ovate env | `CORS_ALLOWED_ORIGINS = ["https://dawncast.pages.dev"]` |
| gotrue-mon env | `GOTRUE_SITE_URL` + `GOTRUE_URI_ALLOW_LIST` 加 Pages URL |

Google Cloud Console → Credentials → Create OAuth client（Web app），Authorized
redirect URIs 加 `https://gotrue-mon.zeabur.app/callback`（裸 `/callback`——gotrue
standalone v2 沒 `/auth/v1/` prefix）。拿到 Client ID
+ Secret 灌進 gotrue-mon env（§7.2）。重啟 gotrue-mon 吃新 env。

**Pages env 設完不會自動 rebuild**——要 Cloudflare API PATCH +
`POST .../deployments` trigger ad_hoc deployment 才會 snapshot 新 env_vars。
完整 curl 在 `memory/zeabur-deploy-resources.md` 「Pages 部署 API + SPA OAuth
flow」段。

### 3.4 驗證

```bash
curl https://api-ovate.zeabur.app/health          # → {"status":"ok"}
curl https://gotrue-mon.zeabur.app/health         # → {"status":"ok"}
curl -i --max-redirs 0 "https://api-ovate.zeabur.app/auth/v1/authorize?provider=google"
# → 302 Location: accounts.google.com/o/oauth2/v2/auth?...
```

---

## 4. JWT 模式：HS256 為何

預設 `SUPABASE_JWT_ALG=ES256`（從 Supabase JWKS 拿公鑰驗簽），但 **Zeabur GraphQL
env var injection bug**（詳 memory `zeabur-deploy-resources.md`）會腐化 gotrue
的 ES256 keys → 啟動失敗。**結論：prod 走 HS256 shared secret**：

```
# api-ovate
SUPABASE_JWT_ALG=HS256
SUPABASE_JWT_SECRET=<強 secret>

# gotrue-mon（同值）
GOTRUE_JWT_SECRET=<同上>
```

Backend 解碼：`backend/app/deps.py:93` HS256 branch。`shared/config.py:212`
`assert_secure()` 會拒 prod 用預設哨兵 secret（避免開後門）。

---

## 5. Auth reverse proxy（`/auth/v1/*`）

### 為什麼需要

- SPA 用 `supabase-js` SDK（`frontend/src/lib/supabaseClient.ts`），內部拼
  `${SUPABASE_URL}/auth/v1/{path}`
- Standalone `supabase/gotrue:v2.189.0`（marketplace）**只認** `/authorize`、不認
  `/auth/v1/authorize`——Supabase gateway 才會加 `/auth/v1/` prefix
- 直接從 SPA 打 gotrue URL → 404

### 解法

api-ovate 加 reverse proxy：`backend/app/routers/auth_proxy.py`（80 行）：

```
/auth/v1/{path}  →  http://{GOTRUE_MON_HOST}:9999/{path}
```

- **不 follow redirect**：gotrue `/authorize` 回 302 → `accounts.google.com`，proxy
  把 Location 透傳給 SPA browser 跟隨跳轉（follow 會把 Google response 吞掉）
- 透傳 method / query / headers（過濾 hop-by-hop）/ body
- 內網走 HTTP（容器內不通 TLS），TLS 終止在 Zeabur edge

註冊在 `backend/app/main.py:152`。測試在 `backend/tests/test_auth_proxy.py`。

### SPA 端 0 改動

只要：

```
VITE_SUPABASE_URL = https://api-ovate.zeabur.app   # ← 指 api-ovate，不是 gotrue URL
VITE_SUPABASE_ANON_KEY = <任意非空字串>             # gotrue standalone 不驗 anon key
```

SDK 自動走 `${VITE_SUPABASE_URL}/auth/v1/{path}` → api-ovate proxy → gotrue。
全功能（`signInWithOAuth` / `getSession` / token refresh / `signOut` /
`onAuthStateChange`）繼續用。

---

## 6. 後續部署流程

`git push origin main` 之後：

| Service | 自動 build？ | 觸發 |
|---|---|---|
| `api-ovate` | ✅ GitHub push webhook | Zeabur rebuild `Dockerfile.api`，新 image 部署 |
| `worker-gir` | ✅ GitHub push webhook | Zeabur rebuild `Dockerfile.worker` |
| `gotrue-mon` | ❌ marketplace image 固定 | 升版才動（見 §10） |
| `db-pran` | ❌ marketplace image 固定 | 升版才動 |
| Pages | ✅ GitHub push webhook | Cloudflare rebuild `frontend/`、deploy |

不需跑 CLI、不用 `zeabur deploy --template`——`backend/deploy/zeabur-template.yaml`
留著只是當架構 reference（內容是 marketplace image spec，不是 deploy 命令）。

---

## 7. 環境變數全對照表

以下欄位皆從 Zeabur MCP `get_service_variables` 跟 Cloudflare API 抓的**現況**值。

### 7.1 api-ovate（prod 必設）

```
ENVIRONMENT=prod
CORS_ALLOWED_ORIGINS=["https://dawncast.pages.dev"]
DATABASE_URL=postgres://supabase_admin:<pwd>@db-pran.zeabur.internal:5432/postgres

# HS256（見 §4）
SUPABASE_JWT_ALG=HS256
SUPABASE_JWT_SECRET=<同 gotrue-mon 的 GOTRUE_JWT_SECRET>
SUPABASE_JWKS_URL=https://gotrue-mon.zeabur.app/.well-known/jwks.json  # HS256 path 不讀，但 assert_secure() 要求填
SUPABASE_JWT_AUDIENCE=authenticated

ADMIN_TOKEN=<強 secret>
GENERATION_ENGINE=api_key
FAILOVER_MODE=degrade
API_KEY=<MiniMax token>
API_BASE_URL=https://api.minimax.io/anthropic
API_MODEL=MiniMax-M3

R2_ACCOUNT_ID=<Cloudflare account id>
R2_ACCESS_KEY_ID=<R2 access key>
R2_SECRET_ACCESS_KEY=<R2 secret>
R2_BUCKET=dawncast
R2_ENDPOINT=https://<account>.r2.cloudflarestorage.com

TAVILY_API_KEY=<選填，缺則降級>

APPLY_MIGRATIONS_ON_BOOT=1
POSTGRES_USER=supabase_admin
POSTGRES_HOST=db-pran.zeabur.internal
POSTGRES_PORT=5432
POSTGRES_DB=postgres
```

Zeabur 自動注入 `API_OVATE_HOST` / `WORKER_GIR_HOST` / `GOTRUE_MON_HOST` /
`DB_PRAN_HOST` 給同 project 其他 service 用——不用手設。

### 7.2 gotrue-mon（prod 必設）

```
GOTRUE_JWT_SECRET=<同 api-ovate SUPABASE_JWT_SECRET>
GOTRUE_DATABASE_URL=postgres://supabase_admin:<pwd>@db-pran.zeabur.internal:5432/postgres
GOTRUE_SITE_URL=https://dawncast.pages.dev
GOTRUE_URI_ALLOW_LIST=https://gotrue-mon.zeabur.app,https://dawncast.pages.dev,http://localhost:5173
GOTRUE_API_EXTERNAL_URL=https://gotrue-mon.zeabur.app
GOTRUE_DISABLE_EMAIL_SIGNUP=true
GOTRUE_DISABLE_EMAIL_LINK_SIGNUP=true
GOTRUE_DISABLE_EMAIL_MAGICLINK=true
GOTRUE_DISABLE_EMAIL_OTP=true
GOTRUE_MAILER_AUTOCONFIRM=true

# Google OAuth（見 §3.7）
GOTRUE_EXTERNAL_GOOGLE_ENABLED=true
GOTRUE_EXTERNAL_GOOGLE_CLIENT_ID=<Google Cloud Console>
GOTRUE_EXTERNAL_GOOGLE_SECRET=<Google Cloud Console>
GOTRUE_EXTERNAL_GOOGLE_REDIRECT_URI=https://gotrue-mon.zeabur.app/callback

GOTRUE_DB_DRIVER=postgres
GOTRUE_JWT_AUD=authenticated
GOTRUE_JWT_EXP=3600
PORT=9999
```

### 7.3 Cloudflare Pages（production + preview）

```
VITE_API_BASE_URL=https://api-ovate.zeabur.app
VITE_SUPABASE_URL=https://api-ovate.zeabur.app
VITE_SUPABASE_ANON_KEY=placeholder-self-host-anon-key
VITE_USE_MOCK=false
```

### 7.4 dev only（`backend/deploy/docker-compose.yml`）

```bash
ENVIRONMENT=dev
DEV_AUTH_BYPASS=true                 # dev 本機不繞 Supabase
DEV_USER_ID=00000000-0000-0000-0000-000000000001
CORS_ALLOWED_ORIGINS=["http://localhost:5173"]
```

prod 自動 fail（`shared/config.py:208` `assert_secure()` 拒絕）。

---

## 8. 故障排除

| 症狀 | 看哪裡 | 解法 |
|---|---|---|
| Gotrue `</value>` suffix fatal | Zeabur logs → gotrue | §4 — 改走 HS256 path |
| `relation "identities" does not exist` | gotrue 啟動 log | §3.1 prereqs SQL（search_path + drop partial migrations） |
| SPA 點 Google 登入 404 | DevTools → Network | 確認 `VITE_SUPABASE_URL` 指 api-ovate + api-ovate `auth_proxy` 已 deploy（`backend/app/routers/auth_proxy.py`） |
| `redirect_uri_mismatch` | Google OAuth 同意畫面 | Google Cloud Console `Authorized redirect URIs` 跟 gotrue `GOTRUE_EXTERNAL_GOOGLE_REDIRECT_URI` 字串**完全一致**。注意是裸 `/callback`（不是 `/auth/v1/callback`）—— gotrue standalone 不認 prefix。 |
| Worker BackOff 重啟 | Zeabur logs → worker | DB 健康？`POSTGRES_PASSWORD` 跟 db 一致？`POSTGRES_HOST=db-pran.zeabur.internal`？ |
| Pages build fail「找不到 package.json」 | Cloudflare Pages build log | Root directory 設 `frontend`（不是 repo root）。API PATCH build_config 也行（見 §3.8 類似手法） |
| `auth.users` 沒建好 | psql `\dt auth.*` | §3.1 prereqs SQL 沒跑乾淨，drop `auth` schema CASCADE 重來 |
| ffmpeg 燒字幕變方框 | worker log | Dockerfile.worker 漏裝 fonts-noto-cjk，rebuild worker image |

---

## 9. 已知限制 / 後續事項

- **單點故障**：Zeabur host 死 = 全部 service 死。Mitigation：手動 pg_dump。
- **無 HA、無監控**：個人版不裝 Sentry / Better Stack。出問題翻 Zeabur logs。
- **Mock 媒體 6.4MB**：`frontend/public/episodes/loop_engineering.mp4` 仍隨 Pages
  bundle 出貨。prod 不該帶，待清掉。
- **`jwt-keys/` 已 commit ES256 私鑰**：`backend/deploy/scripts/jwt-keys/` 內的
  `jwt_es256.pem` 是過渡期產物，HS256 path 不需要——**待加 `.gitignore` 隔離、從
  git history 清掉**（不可逆操作要小心，rotate secret 配套）。
- **`manual_backup.sh` 待補**：`backend/deploy/scripts/` 沒有單人版每日 pg_dump。
- **Docker compose 僅供 dev**：`backend/deploy/docker-compose.yml` 是本機 4 容器
  dev 環境；prod 全走 Zeabur marketplace，**不要拿來 deploy**。

---

## 10. 升級 gotrue / postgres

marketplace image 升版流程：

1. Zeabur Dashboard → service → Settings → **Image tag** 改新版號
2. 看 [supabase/gotrue releases](https://github.com/supabase/gotrue/releases) 是否要
   `ALTER ROLE` / schema migration（少見）
3. **Redeploy** → 觀察 startup log
4. 順序：**db → gotrue → api → worker**（前面是後面的依賴，不顛倒）

Postgres major upgrade（17 → 18）需要 dump → 新 instance → restore → 切 DNS，
走 Supabase 官方 upgrade 流程。個人版不常遇到。