# 部署（DawnCast Self-host on Zeabur Server）

單一 host 跑六個容器：**api + worker + db + gotrue + kong + backup**。
build context 一律是 `backend/`（Zeabur Template YAML 也是這樣定）。

> 為什麼選 Zeabur Server Marketplace + Self-host Supabase？
>
> - 全部服務在**同一台主機**，shared host networking，supabase/postgres image 已預裝 pgmq/pg_cron/pgvector 三個 DawnCast 必需 extension。
> - 省成本：HK/Tokyo/Singapore 月費 $3-8（含 CPU/RAM/SSD/0.5TB 流量），Supabase 也照 Cloud 使用方式出帳。
> - 部署簡單：Zeabur 一鍵 deploy、Dashboard 設 secrets、不用碰 K8s、不用管 Cloud Run auto-sleep。
> - 維運集中在一台機器：OS 升級、image 換版、cron 設定都同一台。
> - **單點故障（host 死）→ 不會掉資料**：Postgres + R2 都各有副本；用戶 JWT 含 kid，rotate 0 down time。

---

## 0. 規格 / 月費估算

| 項 | 規格 |
|---|---|
| CPU | 2 vCPU（建議 4）|
| RAM | 4 GB（精簡 Self-host 起步，2 GB 會被 OOM；4 GB 穩跑）|
| SSD | 40 GB（DB seed + audio R2 detached，本機只存 DB dump）|
| 流量 | 0.5 TB/月 |
| Region | HKG1（香港）或 Tokyo / Singapore |
| **合計** | **$3-8/mo** |

額外月費：
- Cloudflare R2（含備份）：~$1-2/mo（音檔 100GB 用量估算）
- Cloudflare Pages（前端）：$0
- MiniMax API 用量：依用量計

---

## 1. 拓樸

```
┌─ Single Server（Zeabur, 4GB RAM）─────────────────────┐
│                                                        │
│  api (Dockerfile.api)         → FastAPI :8080         │
│  worker (Dockerfile.worker)   → python -m engine.worker│
│                                                        │
│  db (Zeabur Postgres 或 supabase/postgres:17) :5432     │
│     ├── pg_cron（evergreen fallback 03:30/22:00）      │
│     ├── pgmq（control / generate / dict_translate）     │
│     └── pgvector（episode embeddings）                 │
│                                                        │
│  gotrue (supabase/gotrue) :9999  (Auth, JWT ES256)    │
│  kong (kong/kong) :8000         (對外 /auth/v1/*)      │
│                                                        │
│  backup (Dockerfile.backup)   dcron 04:00 Taipei       │
│     → R2 bucket: dawncast-backups/postgres/            │
└────────────────────────────────────────────────────────┘

外部服務：
  • Frontend           → Cloudflare Pages（$0；指向 api 對外網域）
  • 音檔 / 圖          → Cloudflare R2（S3-compatible，由 worker 寫）
  • 備份               → Cloudflare R2 bucket dawncast-backups
  • LLM                → MiniMax API（HTTP 對外）
```

---

## 2. 首次部署（從零到跑）

### 2.1 採購 Zeabur Server
1. Zeabur Dashboard → Marketplace → Server → 選 HKG1/Tokyo/SG（**TenCent 29% off**）
2. 規格：4 vCPU / 4 GB RAM / 40 GB SSD
3. 拿到後 Zeabur 會在 dashboard 新增一個 Project，命名 `dawncast-prod`

### 2.2 採購外部依賴
1. **Cloudflare R2**：建兩個 bucket
   - `dawncast`（音檔/字幕）
   - `dawncast-backups`（每日 pg_dump）
   - 在 R2 Dashboard → API Tokens → 產 access key（給 Zeabur env 用）
2. **Google OAuth**：到 Google Cloud Console → API & Services → Credentials → 建 OAuth client
   - Authorized redirect URI 寫 `https://<API_EXTERNAL_URL>/auth/v1/callback`
3. **Cloudflare Pages**：連 GitHub → 設 `npm run build` 為 build command、build output `dist`
   - 設 env：`VITE_API_BASE_URL=https://<API_EXTERNAL_URL>`、`VITE_SUPABASE_URL=https://<API_EXTERNAL_URL>`、`VITE_USE_MOCK=false`

### 2.3 產 JWT signing key
```bash
cd backend/deploy/scripts
./sign-jwt-key.sh ./jwt-keys
# 將 jwt-keys/jwt_es256.pem 整段（含 BEGIN/EC/END 行）複製下來
```
**安全**：這是 OAuth 級的私鑰，**不要 commit**。

### 2.4 部署到 Zeabur
```bash
# 在 backend/ 跑 zbpack（Zeabur CLI）
zeabur deploy --template deploy/zeabur-template.yaml
```

接著進 Zeabur Dashboard → 設定以下 env（在每個 service 的 Variables 頁；同樣的值多份會從上一個繼承）：

| Env | 設定處 | 必設 | 說明 |
|---|---|---|---|
| `POSTGRES_PASSWORD` | db, gotrue, api, worker, backup | ✅ | 同值 |
| `GOTRUE_JWT_KEY` | gotrue | ✅ | 2.3 那一段 |
| `GOOGLE_CLIENT_ID` | gotrue | ✅ | |
| `GOOGLE_CLIENT_SECRET` | gotrue | ✅ | |
| `SITE_URL` | gotrue | ✅ | 前端公開網域 |
| `API_EXTERNAL_URL` | gotrue, api | ✅ | Kong 對外網域 |
| `URI_ALLOW_LIST` | gotrue | ✅ | JSON 陣列字串，含 callback |
| `CORS_ALLOWED_ORIGINS` | api | ✅ | JSON 陣列字串，前端 origin |
| `ADMIN_TOKEN` | api, worker | ✅ | 強祕密 |
| `MINIMAX_API_KEY` | api, worker | ✅ | |
| `R2_ACCOUNT_ID` | api, worker, backup | ✅ | |
| `R2_ACCESS_KEY_ID` | api, worker, backup | ✅ | |
| `R2_SECRET_ACCESS_KEY` | api, worker, backup | ✅ | |
| `R2_ENDPOINT` | api, worker | ✅ | `https://<account>.r2.cloudflarestorage.com` |
| `R2_BACKUP_BUCKET` | backup | ✅ | 預設 `dawncast-backups` |
| `TAVILY_API_KEY` | api, worker | ⚪ | 缺則自動降級 |
| `APPLY_MIGRATIONS_ON_BOOT` | api, worker | ⚪ | 設 `1` 讓容器啟動時跑 migrations |
| `POSTGRES_USER` | api, worker | ⚪ | 要 `supabase_admin` 才能 create extension |

> ⚠️ **重要**：db service 跑起來後**第一次**健康，**立即**把 `APPLY_MIGRATIONS_ON_BOOT=1` 加到 api 跟 worker 重啟，
> containers 會依序用 supabase_admin 連 db 跑完 0001~0009 並用 SQL Editor 看不到也沒關係，
> 之後可改回 0（讓 deploy 速度穩定）。

### 2.5 啟動 backup sidecar
- 在 Zeabur Dashboard 把 `backup` 服務 un-Suspend
- 第一次手動觸發：在 `backup` container shell 跑 `backup.sh`，確認 R2 有新檔案

### 2.6 驗證
1. `curl https://<API_EXTERNAL_URL>/health` → `{"status":"ok"}`
2. `curl https://<API_EXTERNAL_URL>/auth/v1/.well-known/jwks.json` → 公開 JWKS JSON 帶 ES256 key
3. 前端登入頁 → 點 Google 登入 → callback 走 Kong → JWT 拿到 → 進 dashboard
4. psql 進 db container：`\dt` 應該看到 episodes、deliveries、user_vocab、dict_cache 等 9+ 表

---

## 3. 後續部署流程

推到 origin 之後想更新 Zeabur：

```bash
cd backend
zeabur deploy --template deploy/zeabur-template.yaml
```

（暫時還沒接 GitHub Actions——Git 上 push 不會自動 deploy，採手動點。每改 PR 一次 Redeploy 一次。

> ⚠️ 升級 Zeabur image tag 之前：測試 staging 機先演練（見 §5 升級 SOP）。

---

## 4. SOP：JWT Signing Key Rotate

key 洩漏 / 預期過期 / 年度強制 rotate 用這份。

```bash
# 0. 在 staging 機先用 deploy/scripts/sign-jwt-key.sh 演練一遍
cd backend/deploy/scripts
./sign-jwt-key.sh /tmp/rotate-keys     # 產 jwt_es256.pem

# 1. 把現有的 production key 從 Zeabur GOTRUE_JWT_KEY 抓下來備份（Zeabur env edit 不會記歷史）

# 2. 把新 key 貼到 GOTRUE_JWT_KEY，格式：
#    -----BEGIN EC PRIVATE KEY-----
#    ...
#    -----END EC PRIVATE KEY-----
#    保留舊 key 在下面（GoTrue 多 key 模式：所有列出的 key 都接受驗證，
#    新 token 用第一個簽）

# 3. 等 24-48 小時（讓所有用戶的 access/refresh token 換成新 kid 簽的）

# 4. Zeabur Dashboard → gotrue env → 移除舊 key

# 5. 0 down time。**不需要重啟 FastAPI**：JWKS 在驗證時才抓，新 key 一上線就生效。

# 6. 完成後清掉 /tmp/rotate-keys 本地檔
shred -u /tmp/rotate-keys/jwt_es256.pem
```

---

## 5. SOP：Supabase 月度升級

[supabase/supabase releases](https://github.com/supabase/supabase/releases) 大約每月 5-9 號出新版本。

```bash
# 1. 訂閱 RSS：gh release list --repo supabase/supabase --limit 5

# 2. 升 minor（例 2.189 → 2.190）：
#    zeabur-template.yaml 的 gotrue / kong image tag 改新版
#    → 走 staging 機演練一次
#    → 主要看 release notes 是否要 ALTER ROLE / schema migration 跑手動

# 3. 升 Postgres major（17 → 18 要等幾年）：
#    走 supabase 官方 [Upgrade to Postgres 18](https://supabase.com/docs/guides/self-hosting/postgres-upgrade) 流程
#    **不要在 production 直接幹**：做 dump → 起新 instance → restore → 切 DNS → 下線舊。

# 4. 升級順序永遠是：
#    db（image tag 新）→ gotrue → kong → api → worker → backup
#    早 dep 的服務是後面服務的依賴，不顛倒。

# 5. Zeabur Dashboard → Redeploy 整個 project → 觀察 healthcheck
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
#   R2_* 用 dev bucket 的 key

docker compose --env-file .env up -d   # 不含 backup / 不含 migrate
docker compose run --rm migrate        # 第一次要跑；之後容器啟動會自動跑
docker compose --profile backup up -d  # 需要備份時手動打開
```

- 埠號：`db:54322`、`kong:8000`、`api:8080`（都不開 IPv6 對外，避免本機意外滲漏）
- 前端 dev：`npm run dev` 開 vite，本機 5173 → `vite.config.ts` proxy 到 `http://localhost:8080`

---

## 7. 故障排除

| 症狀 | 看哪裡 | 解法 |
|---|---|---|
| api 重啟，worker 起不來 | Zeabur logs → worker | db 健康嗎？`POSTGRES_PASSWORD` 有設嗎？ |
| Google 登入 callback 400 | Zeabur logs → gotrue | `URI_ALLOW_LIST` 含 `$API_EXTERNAL_URL/auth/v1/callback` 嗎？ |
| 前端拿不到 JWT | 瀏覽器 devtools | `curl https://$API_EXTERNAL_URL/auth/v1/.well-known/jwks.json` 有回傳嗎？kong 是不是沒起動？ |
| dict_translate 沒消費 | Zeabur logs → worker | `engine/worker.py:212` 委派 `dict_translate.poll_once`，進 worker poll 迴圈就會消費；測試 `tests/test_pipeline.py:629` 覆蓋。**不是部署缺口**。 |
| 備份的 R2 物件看不到 | R2 Dashboard | `R2_ACCOUNT_ID` / endpoint 確認；`backup.sh` 手動跑一次看 stderr |
| pg_cron 沒跑 | psql → `select * from cron.job_run_details order by start_time desc limit 10;` | DB 時區：`alter database postgres set timezone = 'Asia/Taipei';` |

---

## 8. 已知限制 / 注意事項

| 項目 | 說明 |
|---|---|
| **單點故障** | Host 死 = 全部 service 死。Mitigation：每日 R2 備份（RPO ~24h），Zeabur Dashboard Reboot Server。 |
| **沒有 HA** | Self-host 一台 master。要 HA 上 multi-host，但要負擔多份 Postgres + Zeabur multi-server 設定。MVP 不做。 |
| **共用 Cookie / JWKS** | 升級 gotrue 時新舊 key 並存時段會較長（最多 48h）。可在 §4 縮短回收。 |
| **.env 重置** | 改 `zeabur deploy` 不會 reset Volumes 跟 secrets，但 server 重灌 / 換 server 都要重設 env。 |
| **Studio 不裝** | 不裝 supabase/studio（省 500MB RAM）。需要 GUI 排查時直接 `docker exec -it db psql` 進 db 操作，或臨時起 `supabase/postgres-meta` 容器。 |
| **Postgres image** | 預設走 Zeabur managed Postgres（方便用 auto-backup）。要換 `supabase/postgres:17.6.1.136` 預裝好三個 extension，就把 `zeabur-template.yaml` 的 `db` 段改 `spec.image` 並加 init 註解。 |

---

## 9. 環境變數全對照表（參照）

完整 env 清單含 default / 敏感性見 `backend/.env.example` 跟 `backend/shared/config.py:Settings`。

**必要 prod env（缺一就 fail）**：
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

# 部署時設（Zeabur entrypoint 用）
APPLY_MIGRATIONS_ON_BOOT=1     # 首次 deploy、之後改 0
POSTGRES_USER=supabase_admin
POSTGRES_HOST=db
POSTGRES_PORT=5432
POSTGRES_DB=postgres
```

**dev only**：繞過 auth 的 `DEV_AUTH_BYPASS=true` / `DEV_USER_ID=<uuid>`——production 自動 fail。

---

## 附：舊版 Fly.io 部署

`fly.api.toml` / `fly.worker.toml` / `Dockerfile.api` / `Dockerfile.worker`（Fly 版）
都已不在 production 路徑用，但 `Dockerfile.api` / `Dockerfile.worker` v2（包含 entrypoint）版
跟 deploy/docker-compose.yml 共用同一段，跟舊版差異不大，主要差在「把 scripts/ 帶進 image 並接上 entrypoint」。
Dockerfile 變更可保留作為 backup；實際 deploy 一律走 zeabur-template.yaml。
