# 部署（Fly.io）

兩個獨立 app：**API**（FastAPI 對外服務）與 **Worker**（常駐生成引擎）。build context 一律是 `backend/`。

## 為什麼分兩個 image

| | API（`Dockerfile.api`） | Worker（`Dockerfile.worker`） |
|---|---|---|
| 角色 | 對外 REST，純 I/O | 常駐 poll pgmq，跑寫稿+TTS+ffmpeg |
| ffmpeg | ❌ 不裝（用不到，保持精簡、縮攻擊面） | ✅ 必裝 |
| 中文字型 | ❌ | ✅ `fonts-noto-cjk`（**隱坑**：少了它燒中文字幕變方框，ffmpeg 不報錯） |
| 對外 port | ✅ 8080 http service | ❌ 無 port，常駐 process |
| 縮放 | auto stop/start，保底 1 台 | `auto_stop=false`，常駐不縮 |

兩者皆多階段 build（uv 裝依賴進 `.venv`）、非 root（`appuser`）執行。

## 步驟

```bash
cd backend

# 1. 建兩個 app（首次）
fly apps create dawncast-api
fly apps create dawncast-worker

# 2. 設 secrets（對映 .env.example，兩個 app 各設一次）
# ENVIRONMENT=prod 會啟動上線防呆：漏設 SUPABASE_JWT_SECRET 或 CORS 用 '*' → API 拒絕啟動。
fly secrets set -c deploy/fly.api.toml \
  ENVIRONMENT=prod \
  CORS_ALLOWED_ORIGINS='["https://dawncast.app"]' \
  DATABASE_URL=... SUPABASE_JWT_SECRET=... \
  R2_ACCOUNT_ID=... R2_ACCESS_KEY_ID=... R2_SECRET_ACCESS_KEY=... \
  R2_BUCKET=dawncast R2_ENDPOINT=...

fly secrets set -c deploy/fly.worker.toml \
  ENVIRONMENT=prod APP_TIMEZONE=Asia/Taipei \
  DATABASE_URL=... GENERATION_ENGINE=api_key FAILOVER_MODE=degrade \
  API_BASE_URL=... API_KEY=... \
  R2_ACCOUNT_ID=... R2_ACCESS_KEY_ID=... R2_SECRET_ACCESS_KEY=... R2_ENDPOINT=...

# 3. 部署
fly deploy -c deploy/fly.api.toml
fly deploy -c deploy/fly.worker.toml
```

## Health check

- **API**：Fly http check 打 `GET /health`（`app/main.py` 已提供）。
- **Worker**：容器層 `HEALTHCHECK` 跑 `healthcheck.py` — DB 可連（致命）+ ffmpeg 可執行（致命）+ 生成引擎 health（非致命，暫時不健康靠 evergreen 兜底，不狂重啟）。

## 單點故障與監控

- Worker 是單台。整夜掛掉時，**pg_cron 03:30 的 evergreen 兜底掃描**會把未交付者全補常青集（PRD §6，黎明 SLA 與生成成功率解耦）；Fly 偵測 health 失敗自動重啟。
- **監控指標**（PRD §8.3）：訂閱剩餘配額 %、限流命中率、degrade 觸發次數 —— 任一惡化即評估把 `GENERATION_ENGINE` 切到 `api_key`（env 一鍵切，worker 無需改碼）。

## DB migration

部署前先依 `backend/db/migrations/README.md` 的清單依序套用全部 migration，並啟用 `pgmq` / `pg_cron` / `vector` extension。
