# DawnCast 行為分析埋點清單

> **版本**：v1.0 — Pre-launch 真人研究準備版  
> **目標**：以最小埋點集合驗證 AI 模擬階段的黏著性假設，不追求「全埋」，追求「每個事件都有明確驗證對象」。  
> **事件總數**：18 個核心事件 + 4 個漏斗 + 7 個 KPI

---

## 一、核心留存事件（18 個）

### 分類索引
| 類別 | 事件數 | 驗證假設 |
|------|--------|---------|
| 播放行為 | 5 | 週更 2 集能否支撐週活目標 |
| 查詞互動 | 3 | 查詞 popup 是否為真實護城河 |
| 單字本 / 閃卡 | 4 | SRS 情感鎖定假設是否成立 |
| 內容探索 | 3 | 主題相關性是否為主要留存開關 |
| 進度感知 | 2 | 進度儀表板能否強化續訂理由 |
| 付費轉換 | 1 | 付費牆設計是否造成摩擦或轉換 |

---

### 播放行為

#### `episode_playback_started`
- **Trigger**：用戶點擊播放按鈕，播放器開始播音
- **Key Properties**：`episode_id`, `cefr_level`, `episode_topic_tag`, `user_plan` (free/pro), `playback_speed`, `session_id`, `days_since_last_play`
- **Why It Matters**：驗證「週更 2 集能否支撐每週 2 次回訪」的週活基線假設。`days_since_last_play` 能識別沉默用戶重新啟動的模式。

#### `episode_progress_milestone`
- **Trigger**：播放進度達到 25% / 50% / 80% / 100% 時觸發（不重複觸發）
- **Key Properties**：`episode_id`, `milestone_pct` (25/50/80/100), `playback_speed`, `word_lookups_so_far`, `session_id`
- **Why It Matters**：80% 完播率是「內容值得性」的核心代理指標。配合 `word_lookups_so_far` 可驗證「查詞行為在哪個時間點密集」。

#### `playback_speed_changed`
- **Trigger**：用戶點擊速度按鈕切換播放倍速
- **Key Properties**：`episode_id`, `speed_from`, `speed_to`, `playback_position_pct`, `session_id`
- **Why It Matters**：驗證速度切換 UX 問題的嚴重度——用戶是否因切換困難而減少使用，或速度模式能否成為「用戶嵌入場景」的行為特徵。

#### `seek_15s_backward`
- **Trigger**：用戶點擊 ←15s 倒退按鈕
- **Key Properties**：`episode_id`, `playback_position_pct`, `session_id`, `consecutive_seek_count`
- **Why It Matters**：`consecutive_seek_count`（連續倒退次數）是精聽行為代理指標，也能識別觸控誤觸率。高倒退次數 + 低完播率 = 理解困難而非精聽。

#### `episode_abandoned`
- **Trigger**：播放器頁關閉或 app 背景化，且播放進度 < 80%（明確未完播）
- **Key Properties**：`episode_id`, `abandon_position_pct`, `episode_topic_tag`, `time_in_session_sec`, `word_lookups_before_abandon`
- **Why It Matters**：直接驗證「主題無關性」是否為放棄主因——`abandon_position_pct` 若集中在 0-20% 表示開頭即放棄，高度相關於主題不命中。

---

### 查詞互動

#### `word_lookup_fired`
- **Trigger**：用戶點擊字幕區英文單字，查詞 popup 出現
- **Key Properties**：`episode_id`, `word`, `playback_position_pct`, `lookup_count_in_session`, `lookup_count_lifetime`, `user_plan`
- **Why It Matters**：查詞是「目前唯一差異化壁壘」，此事件量是產品核心價值的直接量化。`lookup_count_lifetime` 累積到閾值（如 50 次）代表肌肉記憶形成。

#### `word_added_to_vocab`
- **Trigger**：用戶在查詞 popup 點擊「加入單字本」
- **Key Properties**：`word`, `episode_id`, `playback_position_pct`, `lookup_to_save_gap_ms`（從查詞到點加入的時間差）, `vocab_book_size_after`
- **Why It Matters**：「查了但沒存」vs「查了就存」的比率，揭示用戶是被動查意思還是主動建資產。這是 SRS 情感鎖定假設的第一步。

#### `word_lookup_quota_hit`
- **Trigger**：Free 用戶當集查詞達到第 3 次（達到 Free 上限），popup 顯示升級提示
- **Key Properties**：`episode_id`, `episode_number_listened_total`, `days_since_signup`, `user_cefr_self_reported`
- **Why It Matters**：直接量化付費轉換的自然摩擦點——用戶在哪集、哪天、查到幾次才撞牆，決定試用期長度與付費牆設計是否合理。

---

### 單字本 / 閃卡

#### `vocab_book_opened`
- **Trigger**：用戶進入單字本頁（底部導航點擊或返回）
- **Key Properties**：`vocab_book_size`, `days_since_last_srs`, `entry_source` (nav_tab / post_episode / other), `session_id`
- **Why It Matters**：單字本是否只是「垃圾桶頁」（開了沒操作）或是主動管理中心，靠 `days_since_last_srs` 確認用戶是否在 SRS 棄用後仍回來查單字本。

#### `srs_session_started`
- **Trigger**：用戶點擊「開始閃卡複習」進入複習頁
- **Key Properties**：`cards_due_count`, `session_id`, `days_since_last_srs`, `vocab_book_size`, `user_plan`
- **Why It Matters**：驗證核心黏著性假設——「用戶是否真的會去用閃卡」。`days_since_last_srs` 是 SRS 放棄診斷的關鍵維度，7 天以上未複習視為棄用風險。

#### `srs_session_completed`
- **Trigger**：用戶完成當次閃卡複習（點「結束複習」或清空到期卡）
- **Key Properties**：`cards_reviewed`, `cards_known`, `cards_unknown`, `session_duration_sec`, `cards_due_at_start`, `completion_rate_pct`（完成/到期）
- **Why It Matters**：`completion_rate_pct` 低（如 < 50%）且 `cards_due_at_start` 高（如 > 20）直接確認「卡片堆積壓力 → 放棄」的假設。

#### `srs_session_abandoned`
- **Trigger**：用戶離開閃卡頁，且當次複習完成 < 80% 到期卡
- **Key Properties**：`cards_reviewed_before_abandon`, `cards_due_at_start`, `abandon_position` (第幾張卡就離開), `session_duration_sec`
- **Why It Matters**：與 `srs_session_started` 配對計算「閃卡啟動但中途放棄率」，驗證閃卡流失不是不開而是開了撐不住。

---

### 內容探索

#### `episode_selected_from_home`
- **Trigger**：用戶在首頁集數列表點擊集數卡，進入播放器頁
- **Key Properties**：`episode_id`, `episode_topic_tag`, `cefr_level`, `list_position` (第幾張卡), `scroll_depth_before_select`, `session_id`
- **Why It Matters**：`scroll_depth_before_select` 配合 `list_position` 揭示用戶探索成本——如果全員只點前 3 張卡，代表篩選功能需求緊迫，而非用戶主動選擇。

#### `home_scroll_no_select`
- **Trigger**：用戶在首頁滑動超過 3 張集數卡，但最終未點開任何集數（超過 30 秒後離開首頁）
- **Key Properties**：`scroll_depth_cards`, `session_duration_sec`, `has_pro`, `days_since_last_play`
- **Why It Matters**：直接驗證「內容探索成本高、開啟即放棄」假設。用戶看了但不點 = 主題無關或資訊不足，是首頁篩選功能的 business case。

#### `locked_episode_tapped`
- **Trigger**：Free 用戶點擊帶鎖頭的集數卡
- **Key Properties**：`episode_id`, `episode_topic_tag`, `tap_count_lifetime`（累積點了幾次鎖頭）, `days_since_signup`, `session_id`
- **Why It Matters**：`tap_count_lifetime` 是付費轉換意圖的最強代理指標——點過 3 次以上鎖頭的用戶推送升級提示轉換率最高。也驗證付費牆的主題分佈是否命中用戶關心的內容。

---

### 進度感知

#### `progress_dashboard_viewed`
- **Trigger**：用戶查看學習進度頁（若已建置）；MVP 前可用首頁學習數字卡的曝光替代
- **Key Properties**：`weeks_active`, `total_listen_minutes`, `total_words_looked_up`, `total_words_saved`, `session_id`
- **Why It Matters**：驗證「進度儀表板是否強化續訂理由」——如果高進度感知用戶的 M1 續訂率顯著高於低感知用戶，則進度儀表板是值得投資的 retention lever。

#### `post_episode_summary_viewed`
- **Trigger**：集數播放完成後，用戶查看「本集學習摘要」卡片（查了幾個詞、新增幾個單字）
- **Key Properties**：`episode_id`, `lookups_this_episode`, `new_words_saved`, `summary_view_duration_sec`, `cta_clicked` (開始閃卡/回首頁)
- **Why It Matters**：驗證「聽後收穫感」能否轉化為當下的閃卡回訪——`cta_clicked = 開始閃卡` 是「情感鎖定飛輪」能否啟動的關鍵節點。

---

### 付費轉換

#### `upgrade_page_viewed`
- **Trigger**：用戶進入方案頁（主動點選底部導航或被鎖頭觸發）
- **Key Properties**：`entry_trigger` (nav_tap / locked_episode / quota_hit / other), `days_since_signup`, `lookup_count_lifetime`, `vocab_book_size`, `user_plan`
- **Why It Matters**：`entry_trigger` 分層分析揭示哪個付費牆設計最有效（自然探索 vs 鎖頭 vs 額度撞牆），指導後續 paywall 優先序。

---

## 二、關鍵留存漏斗（4 個）

### 漏斗 1：首次收聽完播漏斗
**First Listen Completion Funnel**

```
app_open
  → episode_selected_from_home
    → episode_playback_started
      → episode_progress_milestone (milestone_pct=50)
        → episode_progress_milestone (milestone_pct=80)
          → word_lookup_fired（至少 1 次）
```

**Drop-off 假設**：最大流失點在 `episode_selected_from_home → episode_playback_started`。首頁集數卡資訊不足（只有標題和 CEFR 標籤，無情境標籤）導致用戶選了一半猶豫關掉，或因集數卡與個人興趣不相關而放棄選擇。次要流失在 50% 播完前，原因是字幕速度過快或主題不命中導致提前離開。

---

### 漏斗 2：查詞轉單字本資產化漏斗
**Word Lookup → Vocab Asset Funnel**

```
word_lookup_fired（第 1 次）
  → word_lookup_fired（第 5 次，同一集）
    → word_added_to_vocab
      → vocab_book_opened（2 天內）
        → srs_session_started
```

**Drop-off 假設**：最大流失點在 `word_added_to_vocab → srs_session_started`。用戶查了存了，但閃卡複習從未啟動。原因是加入單字本後沒有任何「提醒回來複習」的機制，且用戶不知道 SRS 功能在哪（可發現性低）。次要流失在 `srs_session_started → srs_session_completed`，原因是到期卡數量過多造成放棄。

---

### 漏斗 3：閃卡留存飛輪漏斗
**SRS Stickiness Loop Funnel**

```
srs_session_started（第 1 次）
  → srs_session_completed（第 1 次，completion_rate ≥ 80%）
    → srs_session_started（7 天內第 2 次）
      → srs_session_started（14 天內第 3 次）
```

**Drop-off 假設**：最大流失點在「第 1 次完成 → 7 天內第 2 次啟動」。第一次體驗閃卡後沒有足夠的「未來觸發」（沒提醒、沒到期通知、沒新集播完後的自然入口），加上第一次若遇到卡堆積就會直接建立「閃卡很累」的認知標籤，後續不再觸發。能連續完成 3 次 SRS 的用戶，情感鎖定假設才真正成立。

---

### 漏斗 4：Free 到 Pro 付費轉換漏斗
**Free → Pro Conversion Funnel**

```
episode_playback_started（第 1 次）
  → word_lookup_quota_hit（Free 查詞達上限）
    → upgrade_page_viewed
      → [付費轉換事件]（plan_upgraded）
```

**Drop-off 假設**：最大流失點在 `word_lookup_quota_hit → upgrade_page_viewed`。用戶撞牆後的即時情緒是「煩」，若 popup 不立即導到方案頁或提示太弱，用戶會選擇關掉 app 而非主動去找方案頁。次要流失在 `upgrade_page_viewed → 付費轉換`，原因是方案頁缺乏「我已用過多少次查詞」的沉沒成本視覺化，無法強化「Pro 值得」的判斷。

---

## 三、Dashboard KPIs（7 個）

### KPI 1：D7 / D30 留存率
- **Definition**：安裝後第 7 天 / 第 30 天，當天有 `episode_playback_started` 或 `srs_session_started` 的用戶比例（有任意主動行為即計入）
- **暫定目標**：D7 ≥ 40%，D30 ≥ 20%（參考行動學習 app 市場中位數，無真實基線）

---

### KPI 2：M1 付費續訂率
- **Definition**：Pro 訂閱啟動後第 30 天，仍維持 Pro 狀態（未取消、未到期）的用戶比例
- **暫定目標**：M1 ≥ 60%（利害關係人硬湊數字，需第一批真實用戶校正）

---

### KPI 3：週活率（WAU / 訂閱者）
- **Definition**：每週有至少 2 次 `episode_playback_started` 的 Pro 訂閱用戶比例（對應週更 2 集的理論回訪節拍）
- **暫定目標**：≥ 50%（若低於此值，代表週更節奏無法支撐週活，需每日微任務補足）

---

### KPI 4：SRS 週啟動率
- **Definition**：過去 7 天內有至少 1 次 `srs_session_started` 的 Pro 訂閱用戶比例
- **暫定目標**：≥ 35%（若低於此值，SRS 情感鎖定假設失立，需重新評估閃卡產品策略）

---

### KPI 5：查詞轉存率（Lookup → Save Rate）
- **Definition**：`word_added_to_vocab` 事件數 / `word_lookup_fired` 事件數（以用戶週為單位計算）
- **暫定目標**：≥ 20%（每 5 次查詞至少存 1 個，代表用戶有主動建資產意圖，而非純被動查意思）

---

### KPI 6：閃卡放棄率（SRS Abandonment Rate）
- **Definition**：`srs_session_abandoned` 事件數 / `srs_session_started` 事件數（用戶層級，排除首次啟動）
- **暫定目標**：≤ 30%（若超過此值，確認「卡堆積 → 壓力 → 放棄」假設成立，優先修閃卡每日上限功能）

---

### KPI 7：完播率（80% Completion Rate）
- **Definition**：有 `episode_progress_milestone(milestone_pct=80)` 的播放 session 比例 / 所有 `episode_playback_started` session
- **暫定目標**：≥ 55%（以 3 分鐘短集來說，低於此值代表內容或 UX 有系統性問題，而非個別集數問題）

---

## 四、埋點實施優先序

### P0（Pre-launch 必埋，驗證基本假設）
1. `episode_playback_started`
2. `episode_progress_milestone`
3. `word_lookup_fired`
4. `word_added_to_vocab`
5. `srs_session_started`
6. `srs_session_abandoned`
7. `upgrade_page_viewed`
8. `word_lookup_quota_hit`

### P1（前 2 週真人研究期間補齊）
9. `episode_abandoned`
10. `episode_selected_from_home`
11. `home_scroll_no_select`
12. `srs_session_completed`
13. `vocab_book_opened`
14. `locked_episode_tapped`

### P2（進度功能建置後補入）
15. `progress_dashboard_viewed`
16. `post_episode_summary_viewed`
17. `playback_speed_changed`
18. `seek_15s_backward`

---

## 五、驗證矩陣（事件 × 假設對照）

| 黏著性假設 | 主要驗證事件 | 否定信號（假設失立條件） |
|-----------|------------|----------------------|
| 查詞 popup 是唯一護城河 | `word_lookup_fired` 頻率、`lookup_count_lifetime` | 平均每集查詞 < 2 次，或 Free/Pro 查詞頻率無顯著差異 |
| SRS 情感鎖定成立 | `srs_session_started` 週啟動率、閃卡放棄率 | SRS 週啟動率 < 20%，或 90% 用戶在第 2 週後不再開閃卡 |
| 週更 2 集支撐週活 | `episode_playback_started` 週頻率 | 超過 60% Pro 用戶每週回訪 < 1 次 |
| 主題相關性是留存開關 | `home_scroll_no_select`, `episode_abandoned` (abandon_pct < 20%) | 高流失用戶的放棄時間點集中在首頁而非播放中 |
| 進度感知增強續訂 | `progress_dashboard_viewed` 與 M1 續訂率相關性 | 查看進度頁與未查看進度頁用戶的 M1 續訂率無顯著差異 |

---

*最後更新：2026-06-20*  
*來源：Affinity Map（中期 UX 研究）、利害關係人留存假設訪談*
