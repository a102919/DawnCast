# DawnCast 線上服務操作手冊

> 怎麼控制 prod、用 CLI 工具查看狀態、抓 log、查 DB、驗證媒體檔。
> 對象：自己或下一個 agent。寫法：直白、可貼可改。

---

## 1. 服務總覽

prod 跑在 **Zeabur 個人 workspace `dawncast-personal`**，region tpe1，四個 service：

| Service 名稱 | Service ID | 對外網域 | 角色 |
|---|---|---|---|
| `api-ovate` | `6a5f8db64d439e41ee4d35c4` | `https://api-ovate.zeabur.app` | FastAPI / uvicorn（業務 API） |
| `worker-gir` | `6a5f8db64d439e41ee4d35c6` | （無對外） | pgmq worker（render / upload_artifacts） |
| `db-pran` | `6a5f8db64d439e41ee4d35c7` | （無對外，需 port-forward） | Postgres（Supabase 自架） |
| `gotrue-mon` | `6a5f8db64d439e41ee4d35c5` | `https://gotrue-mon.zeabur.app` | Supabase Auth（goTrue） |

Zeabur 內部 DNS：`api-ovate.zeabur.internal`、`worker-gir.zeabur.internal`、`db-pran.zeabur.internal`。**只從 Zeabur 內網解析得到**，從本機要靠 `zeabur port-forward`。

---

## 2. 兩種操作介面

兩種都能用，互補：

- **`zeabur` CLI**（本機 terminal）：deploy / port-forward / 看 status
- **`mcp__zeabur__*` MCP tools**（Claude 內）：看 log / 拉變數 / 列部署，比 CLI 詳細

### 2.1 `zeabur` CLI

```bash
# 版本
zeabur --version

# 列 workspace 專案
zeabur project list

# 列專案內 service（json 格式最詳細）
zeabur --json service list --name <project-name>

# 服務 port-forward（DB 對外、暫時）
zeabur service port-forward --id <service-id> --enable
zeabur service port-forward --id <service-id>           # 看當前狀態（會印 forwarded IP:port）
zeabur service port-forward --id <service-id> --disable  # 用完立刻關
```

⚠ port-forward 會把 `<service>` 的 port 對外開到 Zeabur 的 egress IP（例：`124.156.x.x:32xxx`），**用完立刻 disable**。forwarded port 每次 enable 都可能換。

### 2.2 MCP tools（給 Claude 用的）

透過 `mcp__zeabur__*` 開箱即用，常用：

| 工具 | 用途 |
|---|---|
| `list-projects` | 列所有 workspace 專案（拿 project ID） |
| `list-services` | 列專案內 service（拿 service ID） |
| `get-service` | 拿 service 詳情（domain、spec、Dockerfile） |
| `get-deployments` | 列部署歷史（含 status、created/started/finished 時間） |
| `get-build-logs` | 拿某次部署的 build log（很長，檔案 70k+ chars） |
| `get-runtime-logs` | 拿 service runtime stdout/stderr |
| `get-service-variables` | 拿所有環境變數（**含 secret 值**，小心使用） |
| `execute-command` | 在 service container 內跑指令（read-only 白名單） |
| `file-dir-read` | 同上，但只允許 `ls / cat / head / tail / find / grep / tree / pwd / whoami / which / file` |

---

## 3. 重要環境變數

`api-ovate` 的關鍵 vars（從 `get-service-variables` 拿，**值在 Zeabur 控制台或 get-service-variables 看，不要 echo 到文件 / chat**）：

| Key | 用途 |
|---|---|
| `ENVIRONMENT=prod` | 觸發 `Settings.assert_secure()` 拒絕預設 secret |
| `ADMIN_TOKEN` | `X-Admin-Token` header 驗證用，呼叫 `/admin/*` 需要 |
| `SUPABASE_JWT_SECRET` | HS256 JWT 驗簽（self-host 模式） |
| `SUPABASE_JWT_ALG=HS256` | 啟用 HS256（預設 ES256） |
| `DATABASE_URL` | `postgres://supabase_admin:...@db-pran.zeabur.internal:5432/postgres` |
| `R2_ACCOUNT_ID` / `R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY` / `R2_BUCKET` / `R2_ENDPOINT` | Cloudflare R2（S3 相容） |
| `API_KEY` / `API_BASE_URL` / `API_MODEL` | 寫稿 LLM（MiniMax anthropic 相容） |
| `MINIMAX_AUTH_TOKEN` / `MINIMAX_TTS_URL` / `MINIMAX_MODEL` | TTS |
| `FAILOVER_MODE=degrade` | 寫稿 LLM 失敗時降級策略 |
| `APPLY_MIGRATIONS_ON_BOOT=1` | 啟動時自動跑 migration |
| `CORS_ALLOWED_ORIGINS` | 前端 origin 白名單 |
| `WORKER_GIR_HOST` / `DB_PRAN_HOST` / `GOTRUE_MON_HOST` | 內部 service discovery |

⚠ `R2_ACCESS_KEY_ID` 被 Zeabur 截斷過（31 chars），見 `tasks/lessons.md` R2 entry。設定時確認長度正確。

---

## 4. API endpoint 一覽

prod URL：`https://api-ovate.zeabur.app`

### 4.1 公開（user JWT）

| Method | Path | 用途 |
|---|---|---|
| GET | `/health` | 健康檢查 |
| GET | `/episodes` | 列出可訪問的集數 |
| GET | `/episodes/{slug}` | 拿單集（含 audio_url signed URL + cues） |
| GET | `/episodes/date/{YYYY-MM-DD}` | 取當日交付集 |
| GET/PATCH | `/me` | 帳號資訊 |
| ... | `/vocab/*`、`/favorites/*`、`/settings`、`/daily-orders/*`、`/activity` | 各自 domain |

### 4.2 Admin（`X-Admin-Token` header）

```bash
ADMIN_URL="https://api-ovate.zeabur.app"
TOKEN="$ADMIN_TOKEN"  # 從 Zeabur env 拿，不要 hardcode
```

| Method | Path | 用途 |
|---|---|---|
| GET | `/admin/jobs` | pgmq 佇列狀態（queue_length / age） |
| GET | `/admin/episodes?limit=N` | admin 視角的集數清單（含 `has_audio`、`audio_r2_key` 不可見） |
| POST | `/admin/eps/generate` | 主動觸發一集 podcast 生成（回 202 + msgId） |
| GET | `/admin/token-usage` | 寫稿 LLM token 用量統計 |
| GET | `/admin/db-health` | DB 健康檢查 |

### 4.3 觸發 admin 生成

```bash
curl -s -X POST https://api-ovate.zeabur.app/admin/eps/generate \
  -H "X-Admin-Token: $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "topic": "verifying subtitle alignment fix",
    "angle": "定義",
    "topicType": "evergreen",
    "lengthTier": "short",
    "cefr": "A2"
  }' | jq
```

回 `{"ok":true,"data":{"idempotencyKey":"...","msgId":31,"status":"queued"}}`。worker 撿到後 ~2-12 分鐘渲染完（依長度）。完成後該集 `audio_r2_key` 不為 NULL。

### 4.4 範例：完整 debug 流程

```bash
# 1. 看 worker 是否正在消化
curl -s https://api-ovate.zeabur.app/admin/jobs \
  -H "X-Admin-Token: $ADMIN_TOKEN" | jq .data

# 2. 看最近生成的集
curl -s 'https://api-ovate.zeabur.app/admin/episodes?limit=5' \
  -H "X-Admin-Token: $ADMIN_TOKEN" | jq '.data[] | {id, title, hasAudio, createdAt}'
```

---

## 5. 查 prod DB（直連）

> API 沒暴露 admin 查詢介面的情境（要看 `script_json.cues` / `audio_r2_key` / `daily_orders` 等）需要直連 DB。

### 5.1 開 port-forward

```bash
# 啟動
zeabur service port-forward --id 6a5f8db64d439e41ee4d35c7 --enable
# 查當前狀態（會印 forwarded IP:port）
zeabur service port-forward --id 6a5f8db64d439e41ee4d35c7
# 用完關掉
zeabur service port-forward --id 6a5f8db64d439e41ee4d35c7 --disable
```

### 5.2 用 psql 連

Zeabur forwarded IP 是 egress IP（例：`124.156.222.89:30321`），不是 localhost：

```bash
# 從 api-ovate env 拿 DATABASE_URL，把 host 換成 forwarded IP
export PGHOST=124.156.222.89
export PGPORT=30321  # 從 port-forward status 拿
export PGUSER=supabase_admin
export PGPASSWORD=<POSTGRES_PASSWORD 從 api-ovate env 拿>
export PGDATABASE=postgres

# 看單集 cues 尾端
psql -c "SELECT slug, (script_json->'cues'->-1->>'end')::float AS last_end FROM episodes WHERE slug = '<slug>';"

# 看音檔 key
psql -c "SELECT slug, audio_r2_key FROM episodes WHERE has_audio = true ORDER BY created_at DESC LIMIT 5;"

# 查 pgmq 佇列
psql -c "SELECT * FROM pgmq.metrics_all();"
```

---

## 6. 抓 R2 物件驗證

prod 用 Cloudflare R2，憑證在 `api-ovate` 的 env vars。從本機用 `boto3` 拉：

```python
# save as /tmp/dl.py; uv run python /tmp/dl.py
import boto3
from botocore.client import Config
import os

s3 = boto3.client(
    "s3",
    endpoint_url=os.environ["R2_ENDPOINT"],
    aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
    aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
    config=Config(signature_version="s3v4"),
    region_name="auto",
)
s3.download_file(
    "dawncast",  # bucket
    "episodes/<uuid>/episode.mp3",  # R2 key，從 DB audio_r2_key 拿
    "/tmp/episode.mp3",
)
```

然後：

```bash
ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 /tmp/episode.mp3
```

> 想直接用 signed URL 給瀏覽器 / curl 拉也行，但 R2 預設要 auth，public bucket 沒開。前端 audio 播放走 API `audio_url`（API 用 worker 簽的 R2 presigned URL）。

---

## 7. 看 log

### 7.1 Build log（部署時）

```bash
# CLI 沒辦法，請用 MCP
mcp__zeabur__get-build-logs --deploymentId=<id>
# deployment ID 從 get-deployments 拿
```

build log 很長（70k+ chars），結果會被 save 到 `~/.claude/projects/.../tool-results/`，用 grep 找關鍵字：

```bash
grep -oE "(error|Error|ERROR|FAILED|exit code)" /path/to/build-log.txt | sort -u
```

### 7.2 Runtime log（服務運作中）

```bash
mcp__zeabur__get-runtime-logs \
  --serviceId=6a5f8db64d439e41ee4d35c4 \
  --environmentId=6a5f8882b0b7a4abeb4e6d54 \
  --projectId=6a5f8882bf12353d8c0ae657
```

無 `--timestampCursor` 拿最新；用 cursor 分頁。

### 7.3 在容器內跑指令（受限）

`mcp__zeabur__file-dir-read` 只能跑 read-only 指令（ls / cat / head / tail / find / grep / tree / pwd / whoami / which / file）。要跑 SQL、寫檔、跑 script 都不行 — 用 port-forward 拉出來在 host 處理。

---

## 8. 常見工作流速查

| 想做的事 | 工具 / 指令 |
|---|---|
| 看 prod 是否活著 | `curl https://api-ovate.zeabur.app/health` |
| 看最近 deploy 狀態 | `mcp__zeabur__get-deployments` |
| 看 api runtime log | `mcp__zeabur__get-runtime-logs --serviceId=...api-ovate` |
| 看 worker runtime log | 同上，`serviceId=...worker-gir` |
| 看 pgmq 佇列 | `curl /admin/jobs -H "X-Admin-Token: ..."` 或 `psql` 查 `pgmq.metrics_all()` |
| 觸發一集生成 | `curl -XPOST /admin/eps/generate`（見 §4.3） |
| 撈最新集數 | `curl /admin/episodes?limit=N -H "X-Admin-Token: ..."` |
| 撈單集 cues | port-forward → psql 查 `script_json->'cues'` |
| 撈單集 audio key | port-forward → psql 查 `audio_r2_key` |
| 下載單集 mp3 | boto3 `s3.download_file`（見 §6） |
| 量 mp3 物理時長 | `ffprobe -v error -show_entries format=duration ...` |
| 比對 cues vs mp3 對齊 | `cues[-1].end` vs `ffprobe duration`，差 < 0.1s = 對齊 |
| 看環境變數 | `mcp__zeabur__get-service-variables --serviceId=...` |

---

## 9. 已知雷區

- **R2_ACCESS_KEY_ID 被截斷**：Zeabur env 字串上限 31 chars，過長會 silent truncate，upload 全部 reject。設定後務必驗證。
- **Zeabur 內部 DNS 從 host 解析不到**：`db-pran.zeabur.internal` 之類只在 Zeabur 內網可解，本機一定要走 port-forward。
- **port-forward 開了就記得關**：對外開了 DB port，雖然有密碼但不該長期暴露。
- **forwarded port 會變**：每次 `--enable` port 可能不同，先 `zeabur service port-forward --id ...`（不帶 flag）查當前值再連。
- **ADMIN_TOKEN 不要 echo 到 chat / doc / commit**：用 env var 帶，要查用 `get-service-variables`。
- **deploy 不會自動跑 migration**（除非 `APPLY_MIGRATIONS_ON_BOOT=1`）。手動改 schema 要確認 prod 也跑過。
- **mock mode** (`VITE_USE_MOCK=true`) 跟 prod 完全脫鉤，調前端 mock bug 不影響 prod。

---

## 10. 相關腳本

- `backend/scripts/generate_one.py` — 觸發一集 podcast 落庫（CLI 版，需 `DATABASE_URL` 直連）
- `backend/scripts/backfill_cues_align.py` — 對既有集 cues 等比縮放對齊 mp3（dry-run / commit 模式，見 `--help`）
- `backend/scripts/inspect_pod.py` — 跳過 pgmq 本地跑 pipeline，debug 用（見 `tasks/lessons.md`）
- `backend/scripts/seed_episodes.py` — 把 fixture 集塞進 DB

---

## 11. 參考連結

- Zeabur 文件：https://zeabur.com/docs
- Cloudflare R2 + S3 API：https://developers.cloudflare.com/r2/api/s3/api/
- pgmq：https://github.com/tembo-io/pgmq
- 專案自己的 Zeabur deploy 經驗：`tasks/lessons.md`（搜 "Zeabur"）
