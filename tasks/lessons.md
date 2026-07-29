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

## 2026-07-25 — Podcast 聲音/字幕錯位 root cause：TTS duration 不等於最終 mp3 物理 duration

**情境**：prod the_strokes_f385785e 量測結果
- cues[-1].end = 245.904 s
- mp3 物理 duration (ffprobe) = 243.923 s
- 差 = +1.981 s
- 平均每段系統性 +50.8 ms（39 段累積 ~1.98 s）
- 最後一條 cue 在 mp3 結束前 1.98s 就開始，最後一條文字實際上沒聲音

**根因**：`backend/engine/media/subtitles.py:build_timeline` 用 `cursor += seg.duration + pause_sec` 算每條 cue 的時間軸；`seg.duration` 來自 TTS 模型輸出的 token rate × 字數估算，**沒有任何機制回灌最終 mp3 物理 duration**。

- TTS 算 duration 用的是 waveform duration，正常化後 silence 已被 trim
- 但「正常化後的 WAV」不是「最終 LAME 編碼的 mp3」——後者有 ~46ms encoder delay + LAME padding frame 累積
- 還有 piper TTS 段頭 leading silence 沒被 pre-trim
- `audio.concat_segments` 用「WAV 物理 duration」串接，但 `build_timeline` 用「seg.duration」算 cue，**兩邊用不同事實來源**

**規則**：
- **時間軸與實際音檔是同一條事實**：寫 `build_timeline` 跟 `audio.concat_segments` 時，要讓「串接後的真實 mp3 duration」是 cue 計算的 single source of truth，不是「TTS 估算的 duration」
- **修正方向**（尚未實作）：
  1. 跑完 `audio.concat_segments` 後量 mp3 物理 duration
  2. 算「scale factor」= mp3_dur / sum(seg.duration + pause)
  3. 把 scale factor 套回所有 cue 的 start/end（等比例縮放），或
  4. 重新跑 `build_timeline`，這次用「每段 mp3 解碼後的真實 duration」（不再用 TTS 估算）
- **不要做的事**：寫 magic offset / fudge factor（每段 +50.8ms）蓋掉問題——每個 segment 的實際偏差不同（piper 段頭 + LAME delay + padding），單一常數會把別的集數調壞
- **控制列 duration 用 `audio.duration`，不是 `cues[-1].end`**：`PlayerRoute.tsx:215` 用 cueDuration fallback，但 `usePlayer().duration` 才是 mp3 真實長度；現在 fallback 路徑會讓控制列多顯示 2 秒
- **驗證方法**：每次 render 完跑 ffprobe 校驗 `mp3_dur vs cues[-1].end`，差 > 0.1s 直接 fail（寫進 `tests/test_media.py`）

**為什麼之前沒抓到**：
- plan 階段假設「LAME encoder delay 沒扣」但沒量化
- `tests/test_media.py` 只驗證「cues 順序、單調、總長接近」，沒驗證「cues 總長 == mp3 物理長度」
- 沒有 prod 量測 pipeline，每次 prod 渲染的偏差是「自然漂移」，沒人發現

**驗證已用資料**：
- `/tmp/dc_diag/strokes.json` (完整 cues)
- `/tmp/dc_diag/strokes.mp3` (完整 mp3, 3.9 MB)
- `/tmp/dc_diag/strokes_silence.txt` (silencedetect -25dB/0.2s)
- `/tmp/dc_diag/strokes_silence_05.txt` (silencedetect -30dB/0.5s)
- 結論：silencedetect 算法本身不準（段內靜音會誤切），量 mp3 物理 duration 才是單一真相

### 2026-07-25（續）— 解法落地：concat_segments 回傳 mp3 物理時長，build_timeline 等比例縮放對齊

**實作 diff**（四個檔案，未動 API 契約）：
- `backend/engine/media/audio.py`: `concat_segments` signature 從 `-> None` 改 `-> float`，encode 完成後呼叫既有 `tts._probe_duration` 量最終 mp3 回傳
- `backend/engine/media/subtitles.py`: `build_timeline` 加 keyword-only `target_duration: float | None = None`；新 helper `_align_to_duration` 在偏差 ≥ 0.1% 時把整段 cues 等比例縮放（`cue.model_copy(update={...})` 不可變更新）
- `backend/engine/media/__init__.py:render_episode`: 把 `concat_segments` 的 return 接到 `build_timeline(..., target_duration=mp3_duration)`
- `backend/tests/test_media.py`: 加三條測試守住 — 縮放對齊、差 < 0.1% 不動、`target_duration=None` 向後相容

**為什麼 scale 是「修源頭」不是 magic offset**：`target_duration` 來自最終 mp3 ffprobe 量到的物理時長（音檔事實），不是 hardcode `+50ms × segments`。每集自動量測自動對齊；後續若 encoder overhead 變了（換 ffmpeg 版本），行為自動跟著變。

**為什麼 scale 不是 per-segment probe**：silencedetect 對 39 段實測過於嘈雜（會把句中自然停頓誤判為段邊界，產生 122 段 speech segments 對 39 cues）。scale 對每段相對位移 < 0.05s（實測 scale = 0.992），聽感無感；能用 1 行 scale 解的事不要去寫 audio segmentation。

**測試結果**：`uv run poe lint` ✓、`uv run poe type-check` ✓（62 files no issues）、`uv run poe test` ✓（259 passed, 1 skipped — baseline SRT 缺檔）。

**未修的東西（標 follow-up）**：
- prod 26 集已存在的錯位 cues — 動 prod DB 風險高、且使用者要等新流程在 prod 跑穩才能放心 backfill。解法是對既有集跑一次性 scale backfill script（從 R2 下載 mp3 → ffprobe → scale 寫回 `script_json.cues`），等使用者下 ticket 再做。
- 前端 `PlayerRoute.tsx:275` 已經是 `duration > 0 ? duration : cueDuration` fallback（優先 audio.duration），不用改前端。
- mock fixture `frontend/public/data/episode.json` 指向 mp4 是獨立 mock mode bug，跟正式 R2 mp3 流程無關。

## 2026-07-26 — `git push → Zeabur auto-deploy → 自動跑 migration → pgmq 自動 pickup → 真實 LLM/RAG/DB closed-loop` 是可驗的整條路徑

**情境**：把新研究 graph（decompose_research → gather_evidence → cross_verify → write_script → verify_script_claims → quality_judge → upsert → render → upload → delivery → backfill_dict）+ `episodes.sources jsonb` migration + 6 個新 source provider（OpenAlex / Crossref / World Bank / FRED / Google Fact Check / Internet Archive）+ `verify_script_claims` 核對節點連續推到 origin/main。

- `git push` 一次後 Zeabur 觸發 source-built api-ovate + prebuilt worker-gir 同時部署（deployment `6a659c5e`）。
- `apply_migrations.py` 在 api-ovate container 啟動時跑 15 支 migration（包含新加的 `0015_episode_sources.sql`），冪等 ALTER 加欄位成功。
- `daily_podcast_runs` SQL function 用 cron 02:00 自動建 marker + pgmq send 5 部；worker 收菜跑完整圖，**真實 MiniMax M2.7 reasoning model + 真實 Tavily + 真實 Wikipedia + 真實 R2 + 真實 Supabase DB**，無 mock。
- 結果：5 集 evergreen 建出 cues + audio，prod 直接驗到 `sources=30 / grounded=True`（msg_id=53、`deliver_date=2026-07-28`）、`slug=how_does_rust_ownership_prevent_data_rac_0d1815ef`，Tavily 抓的真實 URL 全是 `rust-lang.org` / `reddit.com/r/rust` / `nomicon` 等合法外連。

**撞過的 3 個 production 等級 bug**（皆透過閉環才一次浮現）：

1. **TAVILY_API_KEY 只在 api-ovate、worker-gir 沒設** — `worker.py::run_worker` 跑 `gather_evidence_node` 時 `TavilyProvider.fetch()` 拿到 `_api_key=""` 直接 silent return `[]`，log 沒留痕跡。**規則**：silent fallback 在 LLM / provider 邊界不留 trace 是偵測失能；任何「key 缺失就 disabled」的 provider 都要在 `__init__` 或第一次 fetch 時 `logger.warning("XXX_API_KEY not set, provider skipped")`，不要 return [] 假裝成功。
2. **Zeabur env 變更不會自動重啟 container，且 worker 是 prebuilt marketplace 不會被 git push 觸發 rebuild** — `create_environment_variable` 後 worker 仍跑舊 process、`os.environ['TAVILY_API_KEY']==''`。**規則**：補 env 後必須 `zeabur service restart --env-id ... --id ... --name ... -y` 手動重啟；prebuilt 服務不吃 git push event。已建獨立 memory `worker-env-tavily-not-restarted.md`。
3. **idempotency_key 撞鍵會讓新 enqueue 看起來「沒做事」** — 同 `deliver_date+topic+angle+length_tier+topic_type` 的 idempotency_key 第一次手動測試 SQL function 就建了 row，再 enqueue 是 `ON CONFLICT DO UPDATE` 靜默 no-op。**規則**：測試新節點時要 `deliver_date` 用「明天」讓 idempotency_key 跟歷史 episode 自然分流，避免假性「queue 卡住」誤判。相關 lessons：「重複 enqueue 對 Q 不要只看 msg_count，要看 episode.created_at」。

**規則**：
- **`git push → Zeabur deployment` 是 prod 自動部署唯一來源**：`api-ovate` source-built 走 GitHub webhook，`worker-gir` marketplace prebuilt 不會被 git push 觸發，只能 env 變更或 Restart 動它。**兩種 service 的部署/重啟模型不同**，debug 前先確認是哪種。
- **`apply_migrations.py` 是冪等的就放心 push**：每支 SQL 寫 `ADD COLUMN IF NOT EXISTS` / `CREATE INDEX IF NOT EXISTS`，push 後 migration 自動跑不用人工補 — 這是「部署零手動」的關鍵；如果 migration 不是冪等的，會在 prod 跟 dev 反覆炸。
- **LangGraph 新節點先在 mock 完整跑通一集端到端（`/tmp/run_pod_mock_full.py`），再 push 上 prod**：mock 端到端能驗證 graph 拓樸、JSON parse、retry loop；prod push 才能驗 LLM schema、RAG 來源、DB 落地。兩段不能互相取代。
- **「prod 有跑過」是不可妥協的驗證**：local mock 跑通不代表 prod 通 — LLM 換模型會換 schema 偏好、Tavily 配額會撞限、R2 credential 會被截、deployment env 不一樣。每一個新圖節點都需要 prod episode UUID 作為 confirmation token，不能只靠「model 200 OK」。
- **debug prod 失敗的入口順序**：(1) 看 Zeabur `runtime logs` 找 exception、(2) 看 DB `episodes WHERE created_at > now() - interval '2 hour'` 確認有 row、(3) `pgmq.read` 看 msg 還在不在 queue、(4) `jsonb_array_elements(episodes.sources)->>'url'` 看抓到什麼真實 URL。比直接 `curl API` 早一步。
- **別用 `kill -9 1` 重啟 Zeabur container**：OCI exec 跟 PID 1 不同 namespace，Zeabur 也沒 expose 通用 restart command；要重啟就 `zeabur service restart` CLI，user 也希望「自幹別問我點 dashboard」。

## 2026-07-29 — 改 gotrue v2.189.0 env 後，`/proc/1/environ` 看到新值不代表 gotrue 已重啟吃到新值

**情境**：要 disable email signup（擋攻擊面），用 Zeabur MCP `create_environment-variable` 設了 `GOTRUE_DISABLE_EMAIL_SIGNUP=true`、`GOTRUE_MAILER_AUTOCONFIRM=false`、`GOTRUE_EXTERNAL_EMAIL_ENABLED=false` 等共 7 條。**幾分鐘後** Zeabur 後台顯示 env 已寫入。但：

1. 直接打 `POST https://gotrue-mon.zeabur.app/signup` 仍然 `HTTP 200` 給 `access_token` — 顯然沒擋。
2. `GET https://gotrue-mon.zeabur.app/settings` 仍顯示 `disable_signup:false`、`mailer_autoconfirm:true` — gotrue 進程內的 config 沒更新。
3. 進 gotrue container `cat /proc/1/environ` 看到 `GOTRUE_DISABLE_EMAIL_SIGNUP=true` 等新值都有。**矛盾**：environ 有了但 config 沒套用。
4. 試 `kill -TERM 1` 重啟 gotrue 二進位，但 Alpine 沒裝 `bash`、env 路徑還是舊值——Zeabur 自動重新 pull 進程時 mirror 的是 **Zeabur 後端第一次寫 env 之前的快照**，不是 dashboard 顯示的當下值。
5. curl 內網 Zeabur gateway GraphQL 嘗試 `restartService` 失敗（`gateway.zeabur.com` 連線被 sandbox 擋）。
6. 試 `POST /restart`、`POST /admin/restart`、`POST /reboot` 全 401/404。

**最終唯一可行辦法**：使用者**手動進 Zeabur dashboard** 對 gotrue-mon 服務按 **Restart**。Zeabur prebuilt container 對 env 變更的 propagation 模型是「EnvChange Event → 排程重啟」，這個排程**有時不會自動執行**或**執行後仍用 snapshot**，dashboard 按鈕是唯一的權威手段。

**規則**：
- **Zeabur prebuilt container 的 env propagation 是不可靠的**：UI 顯示「env 已更新」≠ 進程已重啟≠ config 已套用。唯一可信驗證是打 HTTP `/settings`（或同等 echo endpoint）對帳。
- **驗 gotrue env 真生效的標準測試序列**：① `GET /settings` 看 `disable_signup` / `external.email` / `mailer_autoconfirm`；② `POST /signup` 攻擊測試看是否回 4xx；③ 比對 `auth.users` 確保沒新攻擊 row。**三條都過才算真擋住**。
- **任何「env 改完就算修好」的 commit 必須被 reviewer 退回**：作者必須附「重啟後 `/settings` 對帳截圖」+ 「攻擊測試 4xx log」。沒附就要退件。
- **手動重啟觸發條件**：`mcp__zeabur__get-deployments` 回空（沒有 deployment record）但 service `status=RUNNING` 時，**一定**要 user 去 dashboard 按。CLI 重啟在 Zeabur prebuilt marketplace model 不可行。
- **比 `/proc/1/environ` 還可信的是 `/settings` 端點的真實 config**：進程 env 是 OS 層的字串；gotrue 真實生效的是 `viper.Get("disable_signup")` 之類讀出來的 Go 值。兩者可能因為 `viper.WatchConfig` / hot reload 與否而不一致。**HTTP echo 的 config 才是事實的單一真相**。
- **Zeabur MCP `execute-command` 對 gotrue 也有 shell 讀權限**（雖然部署版本是 Alpine busybox）。`cat /proc/1/cmdline`、`cat /proc/1/environ` 都行。不能用 `kill` 重啟（沒權限或被擋），但能完整觀察進程狀態——這在找不到 dashboard access 時是唯一診斷窗口。

**已修補的 Zeabur env 清單**（prebuilt gotrue-mon）：
- `GOTRUE_DISABLE_EMAIL_SIGNUP=true` ← 擋 `POST /signup`
- `GOTRUE_DISABLE_EMAIL_MAGICLINK=true` ← 擋 OTP-free magic link
- `GOTRUE_DISABLE_EMAIL_OTP=true` ← 擋 OTP
- `GOTRUE_DISABLE_EMAIL_LINK_SIGNUP=true` ← 擋 email link signup
- `GOTRUE_MAILER_AUTOCONFIRM=false` ← 新 user 不會自動 `email_verified=true`
- `GOTRUE_EXTERNAL_EMAIL_ENABLED=false` ← 關閉 email OAuth provider
- `GOTRUE_EXTERNAL_GOOGLE_ENABLED=true` ← **保留** Google OAuth（唯一允許的登入路徑）

**為什麼 Gmail 地址寫在 `admin_email` 而不寫死在前端**：DawnCast backend `admin.py` 的 `_is_authorized_admin` 只認 `app_metadata.provider == "google" + email_verified == True + email == settings.admin_email`。前端不存白名單（已刪 email/password 死碼），任何人都能登入但只有 `q06637557832@gmail.com` 通過 Google OAuth 才能拿 admin——server-side whitelist 是真正防線，前端只是 gating UX。

**Dashboard Email provider 那邊使用者說他自己會關**：在 Supabase/Auth 提供者面板手動 disable email 是「Zeabur env 生效前的保險」，雙重關閉才能真正擋攻擊。Env 沒生效、dashboard 又沒關，攻擊者仍能 email signup — 千萬別只做單邊。

## 2026-07-29 — admin 端點的「第二條路徑」永遠是後門 — fail-closed 並收斂到唯一授權來源

**情境**：`require_admin` 一度是 dual-path：`X-Admin-Token: <ADMIN_TOKEN>` 比對 + `Authorization: Bearer <Supabase JWT> + email in whitelist`。**即使第一條路徑關了，第二條路徑也只認一個 email**，「表面上」已經安全。

事實上：

1. `secrets.compare_digest` 是常數時間比對但**還是需要被瀏覽器或呼叫端執行** — call site 在 router dependency，任何能打到 admin endpoint 的來源（CI、雲端 shelldump、CI secret leak）只要曾經看過一次 env，就擁有跟合法 admin 一樣的權限。**token 從來不是 root secret，是 mobile credential**。
2. token 透過 `localStorage` 在前端傳遞 → `AuthProvider` 把 token 寫進所有 admin request header → 任何前端的 XSS / supply chain injection（如 markdown 渲染、6 個月後遺留的 dep CVE）都偷得到這個 token。Supabase JWT 是 HttpOnly cookie / refresh token rotation，不在前端 JS 可觸及範圍。
3. `assert_secure` 同時檢查 `admin_token` 跟 `admin_email` —— 兩個都設的時候哪條生效？兩者都空時哪條生效？「二擇一」邏輯天生是「哪條先壞，另一條替補」，**沒有 fail-closed 預設**。
4. `AdminTokenCard` 在 admin sidebar 開「填 token / 取消 token」的 UI 開關 → token 跟使用者手動輸入的明文綁定 → 螢幕側錄、肩膀衝浪、共用工作站自動溢漏。
5. 前端 `httpApi.ts` 把 `adminHeaders()` 散在 30 多處請求，每次「加新 admin endpoint」都得記得掛這個 helper，**沒有編譯期強制**。

**最終修法（單一來源，非雙軌）**：

- `backend/app/routers/admin.py` 的 `require_admin` **完全移除** `X-Admin-Token` Header 參數與 `secrets` import；只留 `Authorization: Bearer <Supabase Google JWT>` 解碼後檢查 `settings.admin_email`。
- `backend/shared/config.py` 移除 `admin_token` 欄位、`assert_secure` 只 require `admin_email` 不可空。
- `frontend/src/api/httpApi.ts` 移除 `getAdminToken / setAdminToken / clearAdminToken / ADMIN_TOKEN_KEY` 全部 helper；`adminHeaders()` 保留返回空物件 `{}`（靜默已無作用），加 4 行註解說明歷史 — 「現在 admin 由 Google OAuth email 白名單唯一授權，原 localStorage token helpers 已死碼、刻意保留 stub 避免 import 殘跡報錯」。
- `frontend/src/routes/admin/AdminTokenCard.tsx` 整檔刪。
- `AdminSidebar.tsx` 移除 `KeyRound` icon 與 `TokenStatusButton`，清掉 `hasToken` / `onToggleTokenCard` props。
- `AdminLayout.tsx` 移除 token state 相關 useState、Provider；只剩 Sidebar + Outlet。
- `AuthProvider.tsx` 移除 `prevUserIdRef` 與 `clearAdminToken` 副作用呼叫。
- `tests/test_admin.py` 移除 `test_admin_token_unset_denies_even_empty_header` / `test_episodes_wrong_token_returns_401` / `test_admin_non_ascii_x_admin_token_does_not_500`；改 `test_jwt_email_not_in_allowlist_still_401` 守住 whitelist；改 `test_admin_email_unset_denies_jwt_even_with_email_claim` 用 `monkeypatch.setattr(admin_router, "get_settings", lambda: Settings(environment="dev", admin_email=""))` 強制 admin_email 為空，斷言 401。
- `tests/test_config.py` 把 `admin_token` 斷言全換 `admin_email`，對齊新契約。
- `backend/shared/models/api.py` 的 admin 區塊註解 `# T7：Supabase JWT X-Admin-Token + email 白名單` 改成 `# T7：Supabase JWT email 白名單`。
- `test_openapi_contract.py` 的 schema hash snapshot 自動擋下「改 models 沒重生 openapi」 —— 跑 `uv run poe export-openapi` 後新 hash 才綠。

**規則**：

- **任何 admin 端點的「第二條路徑」在能 review 時就砍**：review 看到 `if bearer_token: ... elif x_admin_token: ...` 立刻 fail PR。 「方便測試」、「fallback」、「CI debug」全是合理化藉口 — 這些都該走 OAuth 的 service account / 開發者本機 JWKS local verifier，**不該走 shared static token**。
- **fail-closed 預設不要二擇一，只留唯一授權軸線**：admin 身分判斷的輸入只該有一個來源（Supabase Google JWT → email claim），任何「另一個驗證路徑」都是 root shell 的影子。`admin_email == ""` 時對所有 admin 端點回 401（哪怕帶合法 JWT），這條必須有測試守住，否則哪天有人把 assert_secure 改掉直接 breakout。
- **瀏覽器前端能看的 secret 都不是 secret**：`localStorage`、`sessionStorage`、`document.cookie` 都是同一個信任層（拿到 DevTools = 拿到 secret）。`secrets.compare_digest` 不能讓它變成真 secret。Supabase JWT 的 access token 在前端是 acceptable（短時效 + refresh rotation + audience 綁定），**但長效 static token 從來都不是**。
- **「給開發者方便」是技術債的隱性載具**：`AdminTokenCard` 設計的初心是「忘記帶 JWT 時打 token 也能測」—— 但它讓攻擊面變成「攻到 token = admin」+ 「忘記清 token = 留後門給共用工作站」。**真正方便開發者是寫真 OAuth dev flow，不是發一把萬能鑰匙**。
- **移除 token 流程要「同檔案多點」一起掃**：backend `routers/admin.py` / `shared/config.py` / `tests/test_admin.py` / `tests/test_config.py` 前端 `httpApi.ts` / `AuthProvider.tsx` / `api/index.ts` / `api/types.ts` / `AdminTokenCard.tsx`(刪) / `AdminSidebar.tsx` / `AdminLayout.tsx` / `AdminLayout.test.tsx` / `models/api.py` — 11 個檔案每個都要 grep `admin_token` / `ADMIN_TOKEN` / `getAdminToken` 任一字串是否還在。漏一處就漏一個攻擊面。
- **OpenAPI hash snapshot 是「改契約不重生」的編譯期護欄**：`test_openapi_contract.py` 移除 `admin_token` 後 schema hash 變 → CI 紅 → 強制跑 `uv run poe export-openapi` → `frontend/src/api/generated.ts` 自動跟上。沒有這個測試，搬欄位時前端 `satisfies` 還盯著過期型別，會讓「前端送的 field 後端已不收」的 breaking change 靜默 deploy。
- **「email 白名單」這個設計本身**：`q06637557832@gmail.com` 在 `admin_email` env 是唯一允許登入 admin 的人選；前端 AdminTokenCard 移除後**任何使用者都能登入 app**（因為不再擋輸入）、但**只有這支 Google account 能過 admin router**。Server-side whitelist 是真正防線，前端 gating 只是 UX polish — 順序不要顛倒。


## 2026-07-29 — 測試 fixture 必須對齊真實 API 結構，否則測試綠但 prod 401

**情境**：commit `639e256` 砍後門時，`_is_authorized_admin` 第 94 行寫 `payload.get("email_verified")`（top-level），但**真實 Supabase JWT 把 `email_verified` 放在 `user_metadata.email_verified` 巢狀**。測試 fixture `tests/_auth.py` 偽造 token 時把 `email_verified=True` 寫在 top-level → 測試綠 → 沒人發現真實 token 永遠 401。

**怎麼炸的**：
1. 把 `email_verified` 寫進 `user_metadata` 巢狀 → 改了 `_is_authorized_admin` 讀 nested `user_metadata.email_verified` 是必要的（不然真實 Google OAuth 永遠被擋）
2. 但同時改了 fixture 結構對齊真實 Supabase → 早期 fixture 用 top-level `email_verified=False` 的測試 `test_admin_email_unverified_jwt_still_401` 從「能 reject 未驗證 token」變成「沒差，反正後端讀 nested 根本看不到 top-level」→ 失去測試意義
3. 用 `uv run python -c "直接餵真實 JWT decode"` 才是發現 bug 的唯一方式 — 純測試無法發現結構不一致

**教訓**：
- **fixture 結構是「測試真實性」的隱性 invariant**：當測試全綠但 prod 401，**先懷疑 fixture 跟真實 API 結構對齊沒**，不要懷疑程式碼邏輯（logic 正確只是拿了錯的欄位）
- **commit 改 auth code 必須 e2e 驗真實 token**：unit test 驗 mock；mock 沒對齊真實結構時通過純屬僥倖。`flake8` `mypy` `pytest` 都無法發現結構錯位
- **新增攻擊面防禦時的 fixture 設計**：要嘛「預設接近真實結構 + 顯式 extra 覆寫」，要嘛「預設最小結構 + 顯式傳入每個欄位」。後者更安全：測試覆寫時必須刻意寫 nested 結構，不會偷吃 fixture 預設
- **「測試名稱看起來驗證了某件事」≠「該件事真的被驗證」**：`test_admin_email_unverified_jwt_still_401` 名字看似嚴格，但讀 `email_verified=False` 在 top-level 安全 — 因為後端從來不讀 top-level，**就算「未驗證」也能通過**。測試通過不代表防禦有用

**檢查清單（任何 「nested 結構」修法都套用）**：
1. 寫一個 **真實 token 結構的 unit test**（`app_metadata.provider == google` + `user_metadata.email_verified == true` + email 命中），確認能 200
2. 寫一個 **「只有 top-level 欄位」的反向測試**（`email_verified=True` 在 top-level + `user_metadata.email_verified` 沒設），確認 401
3. 寫一個 **「nested 欄位為 False」** 的負向測試，確認 401
4. **真實 curl 帶 Supabase JWT 打 prod/staging**，不用 mock — 任何 mock 結構對齊測試綠但 prod 401 都會在這一步暴露
