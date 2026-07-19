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
