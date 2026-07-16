# DawnCast 個人化生成引擎 — PRD 規劃 Todo

## 已確認的產品決策（2026-06-16）
- [x] 內容定位：永遠是英語學習 podcast（B1 英語對話 + 中文字幕），主題只是題材
- [x] 生成/分發模式：夜間預約批次制 + 跨使用者去重 + 黎明遞送
- [x] 防重複：同使用者重複主題 → 產生不同角度；可重用別人生成、他沒聽過的集
- [x] 生成引擎：訂閱制 MiniMax / Claude Code 為唯一引擎（須誠實標示 ToS 風險）

## 研究與撰寫
- [x] 平行研究 7 個維度（engine / vectordb / dedup / novelty / competitive / pipeline / datamodel）
- [x] 對抗式驗證高風險主張（ToS 可行性、成本節省數學、去重門檻現實性）
- [x] 綜合出 MVP 範圍 + 架構 + 資料模型 + 決策表 + 分期路線
- [x] 撰寫正式 PRD 文件（繁體中文）→ `.hermes/plans/2026-06-16-dawncast-engine-prd.md`
- [x] 完成前驗證：對抗式驗證已推翻 3 個原始假設並吸收進 PRD

## 待 Alan 拍板（PRD §8 / §14）
- [x] 生成引擎策略：**拍板＝訂閱+OpenClaw 主引擎 + `api_key` 可逆 fallback**（2026-06-16）。限流→degrade evergreen；計費前提被拆→一鍵切 api_key
- [ ] onboarding 大方向主題的粒度與數量
- [ ] 預約截止與黎明交付的精確 SLA

## 引擎決策的合規義務（PRD §8 隨身條款，落地時逐項顧）
- [ ] adapter 必須可逆：`api_key` 與 `minimax` 並存、env 一鍵切，不可寫死只有 minimax
- [ ] 訂閱 key 只在夫妻帳號內用：不池化、不分享、不轉售 access
- [ ] 落地前實機開 platform.minimax.io ToS 逐字核「規避費用 / 限流」條文（研究全是搜尋擷取、JS 動態渲染未逐字核）
- [ ] 批次控在限流容忍內：單帳號、避尖峰（工作日 15:00–17:30）、跑凌晨；撞窗即 degrade evergreen
- [ ] 監控：訂閱剩餘配額 %、限流命中率、degrade 觸發次數——惡化即評估切 api_key

## 驗證發現的關鍵修正（已寫進 PRD）
- **【2026-06-16 第二輪，Alan 反駁觸發】§8 自我校正**：Alan 指出「MiniMax Coding Plan 可串接 OpenClaw（小龍蝦）等客戶端」。對抗式查證結論 = **Alan 對了一半**：①串接機制屬實、吃訂閱定額、官方支援、能省按量費為真；②但先前 §8 寫的「MiniMax 明文禁 batch / 直接違約 / 封號=存亡級」**過火且錯誤**——MiniMax 條款無此明文，batch 字眼只在 fair-use 限流語境。已把理由改成三支柱：規避費用條款定性 + 供應商趨勢收緊 + 省的錢微不足道（單集文本 $0.005–0.014）。結論方向（按量 API 預設 + adapter 可逆）不變。
- cosine 0.85 去重門檻對 text-embedding-3-small 是錯的（不相關平均 ~0.43，應 0.40–0.55）
- 成本槓桿在 TTS 不在 LLM；去重真正價值是 novelty + 省運算，不是省 LLM
- POC 硬阻塞：translations 硬寫在 generate_subtitles.py，須把 zh 移進 script JSON

## 已拍板的模組決策（2026-06-16）
- [x] **點擊查字 + 單字庫（PRD §7.5）**：ECDICT (MIT) 主字典、CMUdict 補 IPA、Piper TTS 自合音檔；word-level 點擊、側欄 drawer；單字本 server-side 綁 user_id。**避開** Wiktionary / CC-CEDICT / FreeDict en-zh（皆 share-alike / GPL 對閉源商業產品禁區）。MVP In-Scope 已新增「點擊查字 MVP」項；R8/R9 風險已加入 §13

---

# 後端未完成實作（2026-07-16 盤點）

訂閱已先拿掉（前期全功能免費開放）。下面 9 項是新對話要逐項實作的 backlog。

完成定義：每項都給「檔案 / DoD / 驗證」，可單獨開對話執行。

---

## P0 — 影響產品能不能跑

### [ ] T1. 每日生成排程接上（最關鍵）

- **目的**：使用者送訂單後，自動 fire pipeline 產生當日 episode；不再卡在 `pending`/`queued`
- **現況**：`backend/engine/worker.py` 有 `_orchestrate`、`run_generate_job`，但沒有 cron / 排程觸發；`scripts/` 是手動跑的
- **影響檔案**：
  - 後端新增 `backend/app/routers/jobs.py`（POST /jobs/orders/{date}/generate）
  - `app/main.py` 註冊新 router
  - `app/deps.py` 已有的 `get_current_user` 復用
  - 前端 `DailyOrderProvider` 的 `setOrder` 送出後呼叫新 endpoint
- **DoD**：
  - 使用者送訂單 → backend 收到 → enqueue job → 回 202 Accepted
  - worker 在背景跑 `run_evergreen` / `run_generate_job` → 寫入 `episodes` + `deliveries`
  - `/daily-orders/{date}/episode` 重新查得到
- **驗證**：
  - `curl -X POST /jobs/orders/2026-07-16/generate -H "Authorization: Bearer ..."`
  - 過幾分鐘查 `/daily-orders/2026-07-16/episode` → 回 200 而非 null
  - `pytest backend/tests/test_jobs.py`（新寫）

### [ ] T2. 學習進度 / Activity 上雲

- **目的**：ProgressRoute 的 streak、聆聽分鐘、查詞次數、已聽集數、播放進度跨裝置同步
- **現況**：`dawncast:activity:*` 全在 localStorage；無後端 endpoint
- **影響檔案**：
  - 新增 `backend/app/routers/activity.py`（GET/PATCH /activity）
  - DB migration `db/migrations/0008_user_activity.sql`（新增 `user_activity` 表：streak_dates jsonb、listen_minutes jsonb、lookup_count jsonb、listened_episode_ids jsonb、last_played_at、last_played_episode_id、last_played_position）
  - 前端 `ListenedProvider`、`storageGet`/`storageSet` 改成雙寫：寫完 localStorage → fire-and-forget API
- **DoD**：
  - ProgressRoute 顯示的 6 個指標全部改由 API 取得；localStorage 降級為 cache
  - 換瀏覽器登入同一 user 看到的數字一致
- **驗證**：登入 → 操作 → 換瀏覽器登入同帳號 → 數字一致

### [ ] T3. PronounceButton 音檔 backfill

- **目的**：詞卡的喇叭按鈕實際能播發音
- **現況**：`dict_cache.audio_url` schema 有但永遠 null；`engine/media/tts.py` 存在但沒被呼叫
- **影響檔案**：
  - `backend/app/routers/dict.py` 的 `lookup_dict`：查無音檔時呼叫 TTS 回寫
  - `engine/media/tts.py`：對接 Piper TTS（本地）或外部 TTS API；產出 .mp3 上 R2
  - 若要批次 backfill：`scripts/backfill_audio.py` 已存在（看下實作狀態決定要不要改寫）
- **DoD**：隨機抽 10 個單字查 → `dictEntry.audioUrl` 不為 null → 喇叭按鈕出現 → 播放有聲
- **驗證**：前端開 devtools 看 `/dict/lookup` response 含 `audioUrl`；點喇叭可播

---

## P1 — UX 補強

### [ ] T4. 帳號自我管理

- **目的**：使用者可改 email、刪帳號
- **影響檔案**：
  - 新增 `backend/app/routers/account.py`：`GET /me`、`DELETE /me`（cascade user_vocab / user_favorites / user_settings / daily_orders / user_activity）
  - 前端 SettingsRoute 新增「刪除帳號」危險區塊（沿用 confirmClear 的 AnimatePresence 模式）
- **DoD**：刪帳號後該 user_id 所有資料 cascade 清除；再次註冊同 email 視為新帳號
- **驗證**：註冊 → 收 vocab / 收藏 → 刪帳號 → DB 該 user_id 列全空

### [ ] T5. Rate limiting middleware

- **目的**：`/dict/lookup` 走 LLM fallback 會花錢，要擋
- **影響檔案**：
  - `app/main.py`：加慢起步或 token bucket middleware（不引外部 dep，自己寫最簡單 in-memory）
  - `shared/config.py`：加 `RATE_LIMIT_DICT_PER_MIN` 設定
- **DoD**：連點查詞 60 次/分鐘 → 第 61 次回 429 + `AppError("rate_limited", ...)`
- **驗證**：寫 load test 或 `for i in {1..70}; do curl .../dict/lookup?w=test; done` 看 429

---

## P2 — 直接砍 spec / 之後再說

### [ ] T6. 從 product spec 砍掉未做的付費功能

- **目的**：避免 spec 跟實作持續 drift
- **影響檔案**：`tasks/` 下若有舊 spec 提到 CSV / Anki、離線下載、AB 重複練習，標 `[已取消]` 或刪除段落
- **DoD**：grep `tasks/`、`docs/`、`ux-research/` 對這三個關鍵字全部有「已砍」標註
- **驗證**：`grep -rn "CSV\|Anki\|離線下載\|AB repeat\|AB 重複" tasks/ docs/`

### [ ] T7. Admin / Ops endpoint

- **目的**：internal debug、token 用量查詢、生成狀態監控
- **影響檔案**：
  - 新增 `backend/app/routers/admin.py`（`X-Admin-Token` header 驗證）：`GET /admin/episodes`、`GET /admin/jobs`、`GET /admin/token-usage`
  - DB 加 `is_admin` 欄位或獨立 admin_users 表
- **DoD**：帶 admin token 可查所有 episode / job / token 用量；不帶 → 401
- **驗證**：拿 10 個 user 跑 5 天 → admin endpoint 顯示累計 token 用量對得起帳單

### [ ] T8. 出餐通知（email / push）

- **目的**：到 `settings.defaultDeliveryTime` 提醒使用者有新集
- **影響檔案**：
  - `backend/app/routers/` 加 `notifications.py` 或併入 `jobs.py`
  - 整合 Supabase email template 或外部 email service
  - 前端 Settings 新增「通知偏好」toggle
- **DoD**：訂閱出餐時間 → 收到 email
- **驗證**：註冊 → 設出餐時間 5 分鐘後 → 5 分鐘內收信

### [ ] T9. 後端 router-level 測試補齊

- **目的**：settings、favorites、daily_orders 三個 router 完全沒獨立 test（只有 test_api 部分涵蓋）
- **影響檔案**：
  - `backend/tests/test_settings_router.py`、`test_favorites_router.py`、`test_daily_orders_router.py`
  - 沿用 test_api 的 FakeConnection 模式
- **DoD**：每個 router 至少 3 個 testcase（happy path、授權收斂、無權 401/403）
- **驗證**：`uv run poe test` 全綠、coverage 報表這 3 個 router >80%

---

## 建議執行順序

1. **T1（每日排程）** → 沒這個 daily delivery 是死的
2. **T2（進度上雲）** → UX 最痛，跨裝置歸零
3. **T3（發音音檔）** → 詞卡 UX 半殘
4. T5（rate limit）→ T1 跑起來後擋 LLM 成本
5. T4 / T9 → MVP 完整度
6. T6 → 文件清理
7. T7 / T8 → 真的有規模再說
