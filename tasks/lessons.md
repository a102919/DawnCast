# Lessons — DawnCast

被糾正後記在這裡，寫成規則避免再犯。Session 開始先回顧。

## 2026-07-18 — 本機同時跑多個 postgres instance 時，`.env` 的 `DATABASE_URL` 寫死是隱性定時炸彈

**情境**：DawnCast `.env` 寫死 `DATABASE_URL=postgresql://postgres:postgres@localhost:5432/postgres`，但使用者本機實際裝了 4 個 postgres：docker `postgres-dev`(5432)、brew `postgresql@15`(5432 — 跟 docker 撞)、brew `postgresql@14`(5433)、brew `postgresql@17`(5434 — **這才是 dawncast 真正的 DB**)。結果：
- 後端一直連到 docker `postgres-dev`，裡面**完全沒有 dawncast 表**（vanilla postgres，沒 pgvector/pgmq/auth schema）
- 跑 migration 一直炸（extension 找不到、`auth.uid()` 不存在）
- 折騰半天才發現 dict_cache 1.79M 筆 + tatoeba 13M 筆 + auth schema 全在 5434，使用者從頭到尾不知道這件事
- CORS 修完、proxy 修完，**最後一個 500 是 DB 連錯**——所有上游修正都被這個錯誤掩蓋

**規則**：
- **`.env` 內的 port 不要寫死 magic number**：在 `DATABASE_URL` 上面加 2-3 行註解，寫明「這個 port 對應到哪個 instance」（docker / brew @哪版 / Supabase），哪天 port 被搶 / instance 砍了，下次 debug 不會再花 30 分鐘。
- **migration 跑不起來時第一件事是「確認 DB 是對的那個」**：用 `SELECT current_database(), inet_server_port(), version();` 確認自己連去哪，不要直接灌 SQL 進去。連錯 instance 灌 migration 會把空 DB 灌成半套 schema。
- **debug 順序**：CORS / proxy / auth 都修完還 500 → 99% 是 DB 連錯或 DB 沒表。`\dt public.*` 跟 `SELECT count(*) FROM 核心表` 兩個查詢就能定位。
- **多 postgres 共存的本機環境**：在 `~/.zshrc` 或 README 寫一張表（port / instance / 主要用途 / 哪個專案用），避免每次 debug 都要 `lsof -iTCP:5432-5434 -P -n` 全部掃一遍。
- **「使用者記得在本地有特別建一個 DB」這種口頭線索不要略過**：當所有候選 instance 都「看起來沒資料」時，停下來懷疑「我找的地方不對」，不是「資料不存在」。

## 2026-07-15 — 重用查詢的「正交維度」缺一個就撞錯集

**情境**：Phase 4 發現 `find_reusable_episode`（`backend/shared/db/repo.py:217`）只 WHERE `big_topic` + `user_id`，**完全沒帶 `length_tier`**。意思是使用者今天選「深度知識・長篇」會直接命中三個月前「指定主題・中篇」生成的同一集——Phase 1-3 設計的 tier 軸線在重用決策上被當成隱形。

**規則**：
- **重用 SQL 的 WHERE 必須涵蓋所有「會改變內容差異」的維度**。每多一個生成維度（tier / format / tone / language variant），就問一次「重用時要不要按它分流？」要，就上 WHERE。
- **derived field 不上 WHERE**：`format = resolve_format(topic_type, length_tier)` 是這兩個欄位的函數，WHERE 帶它會跟 `topic_type`/`length_tier` 重複判定，反而會引入「語意飄移」（哪天 resolve_format 換映射，舊資料馬上漏接）。
- **API surface 對齊測試 fixture**：repo 介面加參數後，`tests/test_pipeline.py` 的 `FakeRepo` 一定要同步加（即使丟掉也加），否則介面沒被驗證的「沉默契約」會在下次呼叫時炸。

## 2026-07-15 — Pipeline 新增維度後，整條呼叫鏈都要 grep「有沒有人丟字」

**情境**：Phase 1-3 已經把 `topic_type` / `length_tier` 寫進 LangGraph 內部的 `tone_selector_node` 與 `_structure_block`，但**從 `worker._orchestrate` → `resolve_for_user` → enqueue generate_job** 這條「點餐 → 投影 → 排隊」鏈**完全沒帶**這兩個欄位過去。下游 `tone_selector_node` 永遠吃 `evergreen` / `medium` 預設，Phase 1-3 的設計在實際路徑上等於沒做。

**規則**：
- **新增一個 pipeline 維度時，rule of three 段都要驗證**：① 入口（router/handler 收到）、② 持久化（repo 寫入）、③ 取出後傳遞（worker 解構後呼叫下游）。**每一段**都要 grep 確認沒人默默 drop。
- **寫 function 簽名時 keyword-only 是好朋友**：`resolve_for_user(..., *, topic_type, length_tier)` 比 positional 安全——以後再加 axis，呼叫端不會因為參數順序錯而送錯欄位到錯位置。
- **debug 時找「預設值吃掉真相」的位置**：當某個欄位看起來「怎麼都是預設」，先看 orchestrator 解構 dict 那一行有沒有寫——最容易出 bug 的是 `for row in rows: resolve_for_user(row.big_topic, row.user_id)`，然後下游永遠只看到 default。

## 2026-07-15 — Idempotency key 必須包含所有正交維度，但 derived 別進

**情境**：`upsert_episode_node` 原本 idempotency key 是 `{cluster or deliver:topic:angle}:length_tier`。同 big_topic/angle/length_tier 但不同 `entry_mode`（news vs topic）的兩個請求會撞 key，第二次 reuse 第一次的 episode。修正後加上 `topic_type` 收緊。

**規則**：
- **idempotency key 公式**：`{entity identity}:{axis1}:{axis2}:...:{axisN}`，每個「會讓內容長相不同」的 axis 都要進。
- **derived field 絕不進 key**：`format`、`duration_seconds`（如果之後有）這類「另一欄位的純函數」放進 key 會：① 跟 source dimension 重複判定；② resolve_format 邏輯換了整批舊 key 立刻漏。放 source dimension 就好。
- **key 變更要 grep 既有測試**：改 key 格式時 `tests/test_pipeline.py` 的 `test_generate_job_passes_idempotency_key` 之類斷言字串會直接炸——這是好事，但記得同步 fixture body 帶新欄位，否則測試 fixture 自己先撞 key 失敗、訊息會誤導。

## 2026-07-15 — 向後相容要「三層 trust boundary」各補一次 defaults

**情境**：Phase 4 給 `daily_orders` 加 `entry_mode` / `length_tier`。三個地方都要補 defaults 才不會壞：

1. **DB**：`alter table ... add column ... not null default 'topic'`，舊列自動填。
2. **wire schema**：`SaveDailyOrderBody` / zod `DailyOrderSchema` 兩個新欄位都 `.optional()`，舊 client 送缺欄位不會 400。
3. **前端 state hydration**：`DailyOrderProvider.setOrder` 在 `input.field ?? previous?.field ?? 'topic'` 補——因為 localStorage 的舊單**繞過 wire schema 直接讀**，client 端 hydrate 時 `entryMode` 是 `undefined`，送到 wire 才被 server 補 default，但那已經晚了一步（CollapsedSummaryCard 顯示「undefined・undefined」）。

**規則**：
- **新增 optional wire 欄位時，列出三層 trust boundary**（DB / wire schema / client state），逐層補 default。漏一層就壞一條既有資料路徑。
- **Provider 補 default 的模式**：`input.field ?? previous?.field ?? HARDCODED_DEFAULT`，三段 fallback 順序：使用者這次輸入 → 既有持久值 → 程式常數。
- **不要相信「前端用了新 UI 就不會送舊單」**：localStorage 的舊單可能永遠存在（瀏覽器不被清就一直在）。Hydration 邏輯要寫死。

## 2026-07-15 — pg 預設 cursor 回 tuple，不是 dict_row

**情境**：`post_process.py:34` 的 `r["word"]` 直接炸 TypeError。原作者用 RealDictCursor 是後來才顯式設定的，預設 cursor 是 tuple。

**規則**：
- **`await cur.execute(...)` 後 `cur.fetchall()` 預設回 tuple**。`r["field_name"]` 是 psycopg2 RealDictCursor 才支援的特性；asyncpg 完全不支援；`psycopg` (v3) 的預設是 `dict_row` 但可改。
- **看到 `r["..."]` 配 fetchall/fetchone 就先懷疑**：往上找 `cursor(row_factory=...)` 或 `RealDictCursor` 的設定；沒找到就用 `r[0]` / `r.field`（若 row 工廠設好）。
- **修改既有 tuple-based 程式碼時**：別順手「優化」成 `r["word"]` 想說比較可讀——這會在共享 connection pool 沒設 row factory 的環境下直接壞。要改就**先**改 pool 的 row factory，**再**改呼叫端。

## 2026-07-15 — TS barrel re-export 漏字是隱性斷裂

**情境**：`frontend/src/api/index.ts` 沒從 `./types` re-export `EntryMode` 與 `LengthTier`。下游 `import { EntryMode } from '@/api'` 拿到 undefined、`typecheck` 不會錯（因為 TS 把 import 解析成「找不到的命名空間成員」通常只在 strict 模式炸）。後來讀到原始 types.ts 才發現 export 有寫，是 barrel 漏了。

**規則**：
- **加新 export 到 source module 時，同檔 commit 要 grep barrel 檔有沒有對應 re-export**：`grep -n "NewType" frontend/src/api/index.ts` 一行確認。
- **跨多層 barrel 時（types → index → component）要逐層追**：source 寫了 ≠ index 有 re-export ≠ component 拿得到。三層都要查。
- **考慮過 `export * from`**：但它會把內部 helper 一起洩漏出去，且擋不住「刪了某個 export 但 barrel 還在 export *」的 stale reference。對公開 API 還是手動列舉比較穩。

---

## 2026-06-16 — ToS 主張別把「限流語句」升格成「明文禁令」

**情境**：PRD §8 我寫「MiniMax Coding Plan ToS 明文禁止 batch / custom backend」「直接違約」「封號=存亡級風險」。Alan 反駁「可串接 OpenClaw（小龍蝦）等客戶端」，觸發第二輪對抗式查證。

**錯在哪**：
1. 把 fair-use **動態限流**語句（"throttle ultra-high-concurrency batch / multi-user sharing"）**升格**成「授權級明文禁令」。限流 ≠ 禁止。MiniMax 條款根本沒有「僅限互動式開發、禁止後端自動化」這條，也無「personal use only」。
2. 把**風險嚴重度**講到「存亡級/致命」，卻忽略自己 PRD §5 的 03:30 evergreen 兜底早就把黎明 SLA 跟生成成功率解耦——被限流時是「降級」不是「斷線」。
3. 把 Anthropic 的具名執法（04-04 對 OpenClaw 斷供）**跨供應商外推**到 MiniMax，MiniMax 端其實無封號實證。

**規則**：
- **引條款先分級**：明文禁令（may not / 禁止）vs 非授權級語句（定位描述 "designed for"、公平使用節流、建議事項 "recommended"）。只有第一類能寫「違規/違約」。
- **動詞要咬死**：「面向個人開發者」是定位語不是禁令；「建議 production 走 PAYG」是建議不是強制。別把 designed-for 讀成 prohibited-for-not。
- **風險嚴重度要扣著架構講**：有 fallback/兜底時，最壞後果是 degrade 不是 outage，別寫「存亡級」。
- **跨供應商別外推**：A 廠的執法案例不等於 B 廠也這樣做，缺實證就標「趨勢風險」而非「已知會封號」。
- **動態渲染的 ToS 不算逐字核對**：搜尋引擎擷取 ≠ 官方原文，法務定論前必須瀏覽器實機開頁逐字核。

**對的部分要承認**：Alan 的機制論點為真（串接吃訂閱定額、官方支援、能省按量費）。被打臉時先把對方對的部分講清楚，再講為何結論仍不建議——理由要換成站得住的（規避費用條款定性 + 趨勢收緊 + 省的錢微不足道），不是死守原本錯的理由。

---

## 2026-06-16 — 寫「模組」進 PRD 時，資料來源層與互動設計層要分章節

**情境**：寫單字庫模組進 PRD §7.5 之前，差點把「資料來源授權選擇」和「互動 UX 模式」混在同一段。兩者本質不同——前者是法務與授權合規，後者是產品決策——混在一起會讓讀者無法判斷「我若不同意某個授權，我能不能只改 UX 部分」。

**How to apply**：
- 模組型章節一律分至少三小節：**為何選這套（vs 業界各家）→ 互動設計（產品決策）→ 資料流與資料模型（實作）**。
- 授權矩陣放第一節、UX 表放第二節、schema/介面放第三節，讓反對點落在哪一節一目了然。
- 「業界共通模式」當理由時要標一句來源（Voscreen / LingQ / Language Reactor），別偽裝成你自己做的研究——peer session 的素材是二手資料，不寫進自己的決策依據。

## 2026-07-19 — 改 wire schema（欄位改名 / 型別）時，mock fixture 與 public/ 靜態檔也要同步

**情境**：commit `16d5699 feat: 改用 audio-only 播放並前端做字幕同步高亮` 把 frontend 的 `Episode.videoUrl` 改成 `Episode.audioUrl`、`<video>` 換成 `<audio>`。但 `frontend/public/data/episode.json` 這個 mock fixture 沒跟著改，繼續用 `videoUrl` 指向 `http://localhost:8000/media/quantum-computing-basics.mp4`。結果預設 mock mode 下 `episode.audioUrl === undefined`，`<audio src={undefined}>` 沒拉任何東西，使用者回報「播放完全沒有英文的聲音，只有雜音」。

**規則**：
- **改 schema 欄位名時，列出所有「會產出這個欄位」的來源**：TS type / http schema (zod) / mock API 回傳 / mock fixture (`public/data/*.json`) / DB seed。任何一個漏改，下游就是 `undefined` 開獎。
- **跑 `grep -rn '"舊欄位名"'` 在 commit 前先掃一遍**（不限 .ts/.tsx，JSON / .md / fixture 都要），找出所有字面值出現的地方再決定哪些要 rename、哪些是歷史文件可以保留。
- **mock fixture 是隱形契約**：mock 模式的意義是「在沒有 backend 的情況下 UI 也能跑」，因此它的 JSON 結構必須 100% 對齊「http 模式會收到的 response shape」——任一欄位缺，mock 就會假裝成功但 runtime 拿到 undefined，比 500 更難抓。
- **靜態資產放在 `public/` 下不會被 typecheck 掃到**：Vite TS 設定只 compile `src/`，`public/data/*.json` 連編輯器都不看。改 schema 時一定要手動 grep + 手動改，別以為「typecheck 過就代表全 codebase 一致」。


## 2026-07-20 — Podcast 5 集 enqueue 撞的 3 個 bug 全都是 production 等級

**情境**：user 派「建立 5 部 podcast」任務，直接走 pgmq enqueue + worker 收菜。預期 5-10 分鐘完成，實際花了 1 小時，因為連環碰到 3 個 production bug：

1. **`cluster_id` 是 uuid 型別，enqueue 卻塞字串**：`backend/shared/db/repo.py:146` 的 `upsert_episode` 用 `on conflict (idempotency_key) do nothing`，但 `source_cluster_id` 是 uuid 欄位；塞 `cluster_renaissance_001` → `InvalidTextRepresentation: invalid input syntax for type uuid`。**規則**：enqueue script 寫 body 時，所有欄位都要先看 DB schema 對齊型別；`source_cluster_id` / `deliver_date` / `big_topic` 三個欄位最常撞。
2. **M2.7 是 reasoning model，舊 `max_tokens=4096` 把預算吃完 response 沒 text 區塊**：`engine/pipeline/langgraph_pod/chat.py` 的 `MiniMaxChatModel` 沒顯式帶 `thinking` 欄位，LLM 把 4096 拿去 reasoning 就不吐 text → `EngineError: 寫稿回應 content 無 text 區塊`。**規則**：reasoning model payload 一律顯式 `thinking={"type": "enabled", "budget_tokens": N}` 把 reasoning 鎖在固定值，並把 `max_tokens` 拉到 `reasoning + 預期 text * 2`（給 text 留 buffer）；當前值 `16384` + `4096`。
3. **FK violation 死循環**：自己之前加的 DELETE-on-failure 補償（`update_episode_keys_node` 在 R2 + local fallback 都失敗時刪 row + raise）會把 episode row 砍了，但 LangGraph state 還記得 `episode_id`，下一個 `insert_deliveries_node` 跑 `INSERT INTO deliveries` 就 `deliveries_episode_id_fkey` violation → worker 走 retry → 又卡同一個 FK → 死循環。**修法**：`engine/pipeline/langgraph_pod/nodes.py:916` 包 `try/except ForeignKeyViolation`，當作「這集本輪失敗、不交付」log warning 後略過，不讓 graph 終止。

**規則**：
- **新增 DELETE-on-failure 補償時，要檢查下游所有使用 row id 的節點**：它們必須 catch「parent row 已不存在」這個邊界條件，否則 graph 會 fail-fast 把整條 retry 鎖死。`psycopg.errors.ForeignKeyViolation` 必須顯式 import（不要再依賴 SQLAlchemy ORM 自動轉譯）。
- **reasoning model（claude / gpt-5 / m2.7 / o 系列）的 max_tokens 不等於「text 預算」**：它會在 `output_tokens` 內部分配 `thinking + text`。預期 text 8000 → max_tokens 至少 12288、reasoning budget 4096；預期 text 10000 → max_tokens 至少 16384。
- **podcast script prompt 太長會被切斷**：medium tier (6-8 分鐘 / 8 chapters + facts + vocab) 的完整 JSON 對話要 ~10000+ text tokens。**預設用 short tier** 跑通整條 pipeline；長篇是 V2 quality pass 的範疇，不該跟「能跑出來」混在一起。
- **enqueue script 跟 worker body schema 必須有一個 fixture 對齊**：寫一次性 enqueue script 時，所有必填欄位要對齊 `engine/pipeline/generate_job.py:run_generate_job` 的 docstring（`big_topic, angle?, cluster_id?, deliver_date, user_ids[]`）。cluster_id 是 uuid——產生器用 `str(uuid.uuid4())`，不要自己編字串。
- **production chat 改完先對 LangGraph pod 跑 smoke test 1 集**：直接灌完整 dialogue 端到端，驗 LLM 回應 text 區塊、JSON parse OK、render 出 mp3、FK 不炸。token 預算 + 解析 + 媒體落地三件事一起驗，不要各拆開測（測試 mock 層騙你的）。

## 2026-07-20 — 前後端 URL prefix 的「兩層轉換」沒看清楚，別亂加 prefix 對齊 prod spec

**情境**：前端 `httpApi.ts` 用 `${API_BASE_URL}${path}` 組 URL，dev 時 `API_BASE_URL=/api`，瀏覽器打 `/api/episodes`。
- vite dev proxy 收到後，config 寫 `rewrite: (path) => path.replace(/^\/api/, '')`，把 `/api/episodes` 改成 `/episodes` 才送後端（localhost:8000）。
- 後端 router prefix 是 `/episodes`（沒 `/api`），兩邊搭起來 → 200。

prod 部署時 `API_BASE_URL=https://dawncast-api.fly.dev`，fly.io reverse proxy 負責剝外層 `/api`，後端還是看 `/episodes`。

debug 時看到首頁全 0 集 0 個 0 張，看到 vite proxy 把 `/api` strip 掉送 `/episodes` 給後端 → 直覺反應是「後端少加 `/api` prefix」。**加完反而全炸**——vite proxy 是把 `/api` 剝掉的，後端收到的是裸 `/episodes`，加 `/api` prefix 後變 `/api/episodes` 但 request 是 `/episodes` → 全部 404。

curl 直接打 8000 帶 `/api/episodes` 是 200（用 prefix 加完的版本），但瀏覽器透過 vite proxy 打 5173 拿到 404 → 矛盾信號花了一個小時繞。

**規則**：
- **改 dev 環境的 URL 結構前，先看 proxy/rewrite 是否已經在轉換**：`grep -A10 "proxy" frontend/vite.config.ts` 看 rewrite 規則。proxy 跟後端 prefix 是**兩條獨立路徑**，哪條該剝 `/api` 就只讓那一條負責，不要兩邊都加。
- **debug 時別只看「直接 curl 後端」**：要驗的是「瀏覽器→vite→後端」的整條鏈。直接 curl 8000 跟瀏覽器實際請求差在 path（被 rewrite 過），看到 200 vs 404 的矛盾就要去看 proxy config，而不是改後端 prefix。
- **看後端 access log 不要只信瀏覽器 console**：`tail /private/tmp/dc_backend.log` 看 uvicorn 收到的真實 path（`GET /episodes` vs `GET /api/episodes`），比對 proxy rewrite 設定就知道 mismatch 在哪。
- **「對齊 prod spec」不是無腦理由**：spec 寫 `/api/...` 是給 prod reverse proxy 看的；dev 通常有 vite proxy 處理掉外層。改前先 trace 一輪鏈路確認誰負責剝 prefix。

## 2026-07-20 — 「撈不到資料」反覆發生的根因：API 契約沒有唯一事實來源

**情境**：上面好幾條 lessons（URL prefix、mock fixture videoUrl→audioUrl、TS barrel 漏 export、idempotency key 漏軸）表面上是不同 bug，深度盤查後發現同一個結構性根因：後端 13 個透過 router 曝露的 Pydantic model，形狀被人工手抄到前端最多 4 個地方（`api/types.ts` 手寫 TS type、`httpApi.ts` 手寫 zod schema、`mockApi.ts` 內嵌字面量、`public/data/*.json` mock fixture），彼此間零自動化比對；FastAPI 免費產生的 `/openapi.json` 完全沒被使用。

**修法**：backend/shared/models.py 立成唯一事實來源 → `backend/scripts/export_openapi.py`（`uv run poe export-openapi`）匯出 OpenAPI schema → 前端 `openapi-typescript`（`npm run gen:api-types`）產生 `frontend/src/api/generated.ts` → `httpApi.ts` 每個 zod schema 補 `satisfies z.ZodType<TS型別> & z.ZodType<components['schemas']['X']>`，後端改欄位但前端沒跟上時直接編譯錯誤。`backend/tests/test_openapi_contract.py` 用 schema hash snapshot 防止「改了 models.py 卻忘記重新產生」。

**修的過程中自己又撞了一次同類 bug**：mockApi.ts 的 `getEpisode` 原本 `data as Episode` 盲轉型，改成用 zod 驗證是對的方向，但**直接重用 httpApi.ts 驗真實後端回應的 `EpisodeContentSchema`**——這份 schema 因為要對齊後端 `Episode` model 而要求 `topic`/`cefrLevel`/`isFree`，但 `public/data/episode.json` 是手寫的極簡示範 fixture，從來沒有這些欄位，也不需要（domain `Episode` 型別根本沒有這幾個欄位）。結果 mock 模式 PlayerRoute 直接開天窗「節目資料載入失敗」。

**規則**：
- **「後端 wire schema」跟「mock fixture schema」是兩個不同的驗證目標，不要共用同一份 zod schema**：前者要跟後端 model 的每個欄位對齊（含前端用不到但後端會送的欄位）；後者只要滿足前端 domain 型別實際會用到的欄位。硬共用會逼 mock fixture 塞不相關欄位，或（更危險）逼 mock fixture 驗證失敗。
- **改完 schema 一定要各模式各跑一次**：`VITE_USE_MOCK=true npm run dev` 跟 `VITE_USE_MOCK=false npm run dev`（接真後端）都要手動點開 PlayerRoute，兩條路徑分開驗證，不能只驗其中一條就當作全部過了。
- **OpenAPI `required` 只代表「input 驗證要不要求」，不等於「response 保證有值」**：Pydantic 欄位有 `default`（非 `default_factory`）時，openapi-typescript 會標成非 optional（`defaultNonNullable` 行為），但 `default_factory`（list/dict 等 mutable default）欄位仍標 optional。直接把 `components['schemas']['X']` 拿來取代手寫 app 型別，會讓這類欄位對下游消費者變成噪音式 optional——這種情況改用 `satisfies` 釘住 zod schema 就好，不要動 app 層手寫型別。
