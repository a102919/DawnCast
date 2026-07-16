---
project: DawnCast
doc_type: PRD
title: DawnCast 個人化生成引擎 PRD
date: 2026-06-16
status: draft
version: 1.0
based_on:
  - .hermes/plans/2026-06-16-dawncast-master-plan.md（商業模式）
  - .hermes/plans/2026-06-16-dawncast-subscription-feasibility.md（訂閱可行性）
research_method: 7 維度平行研究 → 對抗式驗證 → 綜合（15 agents）
---

# DawnCast 個人化生成引擎 PRD v1.0

> **本文件範圍**：聚焦「個人化內容生成引擎」——夜間預約、跨使用者去重、防重複（novelty）、夜間批次管線、資料模型、生成引擎與成本。
> 商業模式、定價、行銷、財務請見既有 master plan，本文不重複。
> **產品定位（已鎖定）**：永遠是英語學習 podcast（B1 英語 Alex & Sarah 雙人對話 + 繁中字幕）。使用者指定的「主題」只是題材，輸出格式固定。

---

## 0. TL;DR（一頁總結）

DawnCast 引擎是一個**夜間預約批次制**：使用者晚上預約隔天想聽的主題，沒預約就用 onboarding 大方向主題自動補；系統凌晨把所有請求聚類去重、缺的才生成，黎明前交付。核心資料結構用 `deliveries` 表的 anti-join 把「跨使用者重用」與「同使用者防重發」收斂成同一條查詢，消除特殊情況。

**深入研究後，三個原始假設被推翻，本 PRD 據此修正：**

| 原始假設 | 研究/驗證結論 | 本 PRD 採取 |
|---|---|---|
| 訂閱制 MiniMax / Claude Code 當**唯一**生成引擎、無 fallback | **前提部分成立、但仍不建議當唯一引擎**：串接 OpenClaw（小龍蝦）等客戶端吃訂閱定額屬實、官方支援，「能省按量費」機制為真。但 Claude 2026-06-15 起 headless 已 API 化；MiniMax 端**並無明文禁 batch**（先前寫法過火，已修正）——真正咬字面的是「規避費用 / 規避用量限制」條款，加上供應商政策正主動收緊、且省的錢微不足道（單集文本僅 NT$0.04–0.10）。 | **已拍板（2026-06-16）**：訂閱 + OpenClaw 當**主引擎**、`api_key` adapter 為**可逆 fallback**。限流→降級 evergreen；計費前提被拆→一鍵切 `api_key`。合規義務見 §8。 |
| 去重的價值是「省 LLM 呼叫成本」 | LLM 寫稿是零頭（NT$0.05–1.2/集）。真正成本在 **TTS** 與 worker 機時。 | 去重的價值重新定義為**維持 novelty + 避免同質內容 + 省運算**。MVP 先做「大方向共享 + heard-set 防重發」，向量重用延後到 V2。 |
| 用 cosine 0.85 當去重門檻 | **對 text-embedding-3-small 是錯的**：不相關文字平均才 ~0.43，0.85 會讓幾乎所有東西不被聚類。 | 正確區間 0.40–0.55，**必須用真實中英主題對離線校準**，跨語對與同語對分別定門檻。且嵌入前先用 LLM 正規化成「中英雙語 canonical 字串」把跨語問題前處理消滅。 |

**MVP 第一個硬阻塞**：現有 POC 的中文字幕翻譯硬寫在 `generate_subtitles.py` 的 `TRANSLATIONS` dict，與 script 解耦。不把 `zh` 移進 script JSON 每行，整條無人值守批次根本跑不起來。

---

## 1. 產品脈絡與目標

### 1.1 現況
已完成可運作 POC：`script JSON`（topic / extracted_facts / target_vocab / script 對話陣列）→ edge-tts（Alex/Sarah 雙人英語語音）→ ffmpeg 拼接 → 雙語字幕 MP4 + SRT/VTT/JSON。已驗證一集（loop engineering，3:58）end-to-end 可行。

### 1.2 本引擎要解的問題
1. 讓使用者**指定隔天主題**，沒指定則依其大方向自動產生。
2. 多使用者主題相似時**只生成一次、共用**，降低重複勞動與運算。
3. 同一使用者多天指定相同/相似主題時，產生**不同角度**的內容，且不重發他聽過的集。
4. 整條流程**無人值守**、每天黎明前準時交付（2 人夫妻團隊、極省預算）。

### 1.3 非目標（本引擎不負責）
定價、金流、行銷、原生 App、A2–B2 動態分級、學習進度追蹤——這些在 master plan 或 V1.1+ 處理。

---

## 2. 核心使用流程（夜間預約批次制）

```
晚上（預約截止前）              凌晨批次                      黎明交付
──────────────────           ────────────────────         ──────────────
使用者預約明日主題     ──→    收集所有請求            ──→   產生缺的集
  └ 沒預約 → 用 onboarding      正規化 + 嵌入 + 聚類           存入 episodes/向量庫
     大方向主題自動補           查向量庫：可重用嗎？     ──→   依各人時區黎明推送
  （未指定 + 大方向相同           ├ 可重用 → 只寫 deliveries
   → 共用一集，只生成一次）        └ 不可 → 生成佇列 → 生成
```

- **預約**：使用者在每日截止時間（如本地 23:00）前指定主題；可重複預約、可留空。
- **fallback**：留空者套用 onboarding 選的大方向主題（固定枚舉：科技/科學/商業/旅遊/文化…）。
- **共用**：所有「未指定且大方向相同」者共用同一集——**這是最省、最先做、ROI 最高的去重**（不靠向量、靠分桶）。
- **交付**：依 `users.tz` + `delivery_time` 算各人黎明時刻交付（MVP 先單一交付窗，多時區精細排程延後）。

---

## 3. 系統架構

### 3.1 元件
```
┌─ 用戶端（V1：RSS / 簡單 Web 收聽頁，不做原生 App）
│   讀 Supabase deliveries → 簽章 URL 取 R2 音檔/字幕（owner 授權檢查）
│
├─ 控制層（Supabase Postgres = 唯一真相源）
│   pg_cron        夜間 tick，黎明 SLA 主排程（不用 GitHub Actions）
│   pgvector       topic_vec / content_vec（起步免索引、精確掃描）
│   pgmq           生成佇列（SKIP LOCKED 冪等重試）
│
├─ 生成 Worker（一台廉價常駐容器，Fly.io/Railway ~$5–15/月，含 ffmpeg+python+edge-tts）
│   GenerationEngine adapter（主引擎 minimax＝OpenClaw 訂閱；api_key 可逆 fallback；claude_code 手動）
│   既有 POC：generate_episode.py（edge-tts→ffmpeg）＋ generate_subtitles.py（燒字幕 mp4）
│
├─ 嵌入服務（OpenAI/Gemini API，邊界 timeout connect 5s / read 30s、retry≤3）
└─ 物件儲存（Cloudflare R2，egress 免費，簽章交付）
```

**為什麼是「最無聊」的架構**（驗證一致支持）：
- 資料量數千–上萬筆向量、團隊 2 人、已用 Supabase → pgvector 同庫同備份同運維，**不要**引入 Pinecone/Qdrant/Chroma（專用庫優勢要數百萬筆才顯現，DawnCast 永遠到不了）。
- 排程用 **pg_cron**（DB 內、與資料同源），**不用 GitHub Actions cron**（延遲 10–30 分鐘是常態、高負載被丟棄、repo 60 天無活動自動停用，直接違反黎明 SLA）。
- 算圖（ffmpeg/edge-tts）必須在**常駐 worker**，**不能塞進 Supabase Edge Functions**（256MB / 2s CPU / 無多執行緒，跑不了 ffmpeg）。

### 3.2 夜間批次資料流
```
22:00  pg_cron 開啟收集視窗
23:00  預約截止（各時區以 UTC 統一切窗，用 tz+delivery_time 回推「隔日交付 UTC 時刻」）
  │
  ├─[A] 主題正規化：未指定 → 套 onboarding 大方向；
  │     指定 → LLM 輕量正規化成「中英雙語 canonical 字串 + topic_type」
  │     （把跨語短詞問題在前處理消滅，是去重不漏判的關鍵）
  ├─[B] 嵌入：對 canonical 字串算 topic_vec(512) → topic_requests
  ├─[C] 確定性合併：NFKC + case-fold + 去標點 先消 trivial 重複
  ├─[D] 聚類：先 SQL group by 大方向，再 cosine 連通分量（union-find，n≤數百用 O(n²)）→ topic_clusters
  ├─[E] 重用查詢（一條 SQL，不寫特例分支）：
  │     對每個 cluster 找 episodes 中「未過期 AND 該 cluster 成員多數沒聽過(deliveries anti-join)」可重用集
  │     → 命中只寫 deliveries；未命中 → enqueue pgmq 生成任務
  │
23:30→03:30  Worker 排空佇列（SELECT…FOR UPDATE SKIP LOCKED）：
  │     LLM 寫稿（產含逐行 zh 的 script JSON）→ edge-tts → ffmpeg 拼接
  │     → generate_subtitles.py 燒 mp4 → 自我抄襲關卡(content_vec) → 上傳 R2 → 寫 deliveries
  │     單筆硬超時 8 分鐘；失敗靠 visibility timeout 重試，>3 次轉 dead-letter
  │
03:30  兜底掃描：pg_cron 對「未交付者」全部補 evergreen 常青集
  │     （把黎明 SLA 與生成成功率解耦——保證每個訂閱者早上一定有東西聽）
04:00→各地黎明  依 deliveries.deliver_date + tz 推送/上架
```

---

## 4. 資料模型（Supabase Postgres + pgvector）

六張核心表。**關鍵設計**：`topic_vec`（聚類/重用）與 `content_vec`（novelty）分開存，語意不同混用會互相污染門檻。

### 4.1 `users`
| 欄位 | 型別 | 說明 |
|---|---|---|
| id | uuid pk | |
| tz | text | IANA 時區 |
| delivery_time | time | 本地黎明交付時刻 |
| onboarding_big_topic | text | 大方向主題，決定共享集分桶 |
| cefr_target | text default 'B1' | |
| created_at | timestamptz | |

> 大方向主題粒度**直接決定**共享 fallback 集數與內容單一化風險。MVP 先給固定枚舉。

### 4.2 `topic_requests`（每晚預約）
| 欄位 | 型別 | 說明 |
|---|---|---|
| id | uuid pk | |
| user_id | fk | |
| request_date | date | |
| raw_topic | text | 使用者原始輸入 |
| canonical_topic | text | LLM 正規化的中英雙語 canonical 字串 |
| topic_type | enum('news','product','evergreen','skill') | 不確定一律保守標 fast-decay |
| topic_vec | vector(512) | |
| cluster_id | fk nullable | |
| source | enum('specified','fallback') | |
| created_at | timestamptz | |

> `canonical_topic` 先做 NFKC+case-fold+去標點，再嵌入——把跨語短詞對齊問題在前處理消滅。

### 4.3 `topic_clusters`（夜間聚類）
| 欄位 | 型別 | 說明 |
|---|---|---|
| id | uuid pk | |
| cluster_date | date | |
| centroid_vec | vector(512) | |
| canonical_topic | text | |
| member_count | int | |
| resolved_episode_id | fk nullable | 重用或新生成的結果集 |

> 用 cosine 連通分量（union-find），不用 HDBSCAN（夜間量小，HDBSCAN 的密度自適應反成不穩定來源）。
> **聚類門檻【最關鍵修正】**：text-embedding-3-small 不相關文字平均才 ~0.43，原本想用的 0.85 會讓幾乎所有東西不被聚類。正確區間 **0.40–0.55**，且**必須用 DawnCast 真實中英主題對離線校準**，跨語對與同語對分別定門檻。

### 4.4 `episodes`
| 欄位 | 型別 | 說明 |
|---|---|---|
| id | uuid pk | |
| topic | text | |
| script_json | jsonb | 含每行 `{speaker,text,zh}` |
| audio_r2_key / mp4_r2_key / srt_r2_key | text | |
| extracted_facts / target_vocab | jsonb | |
| big_topic | text | |
| variant_no | int | 同主題第幾個角度 |
| angle | enum('定義','人物故事','常見誤解','應用場景','歷史','對比') | |
| freshness_class | enum('evergreen','timely','dated') | |
| expires_at | timestamptz nullable | |
| topic_vec | vector(512) | 聚類/重用 |
| content_vec | vector(512) | novelty 比對 |
| source_cluster_id | fk | |
| embedding_model_version | text | 換模型時全量重嵌 |
| created_at | timestamptz | |

> `variant_no + angle` 是 novelty 換角度的關鍵鍵。`content_vec` 嵌 extracted_facts 或腳本摘要（待實測選哪個最準）。

### 4.5 `deliveries`（交付記錄 + heard-set 權威來源）
| 欄位 | 型別 | 說明 |
|---|---|---|
| id | uuid pk | |
| user_id | fk | |
| episode_id | fk | |
| deliver_date | date | |
| heard | bool default false | |
| heard_at | timestamptz | |
| position_sec | int default 0 | 續聽用 |
| | **UNIQUE(user_id, episode_id)** | |

> **這張表是消除「重用 vs 新鮮」特殊情況的核心**：重用（重用別人生成、他沒聽過的集）與防重發（不重發他聽過的）用**同一條 anti-join** 解決，禁止為「別人的集 vs 自己的集」寫特例分支。

### 4.6 `user_heard_topics`（novelty 專用 heard-set）
| 欄位 | 型別 | 說明 |
|---|---|---|
| user_id | fk | |
| topic_vec | vector(512) | |
| content_vec | vector(512) | |
| episode_id | fk | |
| heard_date | date | |

> 同使用者多天相同主題時，用 `content_vec` cosine 判斷新生成集是否「角度夠不同」。
> 設**保留窗**（近 180 天或近 N 集）避免線性膨脹；超窗舊紀錄降級成只留 `deliveries` 的 `episode_id`。介面化 `is_heard(user, episode)`，規模化可換 Bloom filter 而不改交付邏輯。

---

## 5. 核心機制一：主題正規化 + 跨使用者去重

**三階段漏斗**（把 LLM 當「仲裁者」不當「聚類器」，把 LLM 呼叫量壓到最低）：

1. **確定性正規化**：NFKC + case-fold + 去標點 + trim → 先消掉 `quantum computing` / `Quantum Computing` / ` quantum  computing ` 這類 trivial 重複。
2. **LLM 輕量正規化**：把自由輸入（可能中/英/口語別名）收斂成「中英雙語 canonical 字串 + topic_type」。同一次呼叫順便產出英文標準詞與 topic_type，省一輪 LLM。**價值在跨語言與口語別名收斂**（量子電腦/量子計算 → quantum computing）。
3. **嵌入聚類**：對 canonical 字串嵌入 → 先 SQL group by 大方向主題、再 cosine 連通分量合併近似 topic → 只對**邊界相似 pair** 呼叫一次 LLM 仲裁是否同題。

**為什麼前處理比調門檻更可靠**：跨語短詞（命名實體、OOV 詞）正是多語嵌入對齊最弱的區段，光調 cosine 門檻治標不治本；先把主題統一成雙語 canonical 字串，從源頭消滅跨語落差。

**去重的真正成本歸因（修正）**：LLM 呼叫已低至約 NT$0.05–1.2/集，「為省 LLM 呼叫而重用」的經濟誘因幾乎消失。去重的真正價值在**避免重複生成同質內容、維持 novelty、省 TTS/ffmpeg 運算機時**。所以 MVP 先做「大方向共享集」（靠分桶、零向量成本）與「heard-set 防重發」，**向量語意重用延後到 V2**（ROI 要到數百請求/晚才翻正）。

---

## 6. 核心機制二：novelty 防重複引擎

**核心張力**：跨使用者「想回收」對上 同使用者「要新鮮」。

**統一解法**——去重主鍵 = `(cluster_id, variant_key)`，疊上每位使用者的 heard-set：

```
交付查詢（單一邏輯，不寫特例）：
  取該 cluster 下「該使用者 deliveries 沒有的」variant，按相關度排序取第一個；
  空集合才進生成佇列，生成 variant_no+1。
→ 「重用別人生成、他沒聽過的集」與「同主題換角度」變成同一條查詢的兩個過濾條件。
```

**新 variant 的差異化靠三件事疊加**（純「叫 LLM 再寫一次」無效——RLHF/DPO 對齊會壓垮 LLM 自然多樣性，純 regenerate 會收斂同質）：

1. **角度輪替**：固定 6 角度 taxonomy（`定義 / 人物故事 / 常見誤解 / 應用場景 / 歷史 / 對比`），存成 `as const` 常數。生成時從「此 cluster 尚未用過的 angle」挑一個強制寫進 prompt。**不要依賴 LLM 自己想角度。**
2. **fact-set diff（排除清單）**：檢索該使用者此主題群歷史集 → 抽出已講 `fact_ids/angle` → 當「avoid list」塞進寫稿 prompt（用 MMR 從候選歷史集挑「與已聽集差異最大」者）。
3. **自我抄襲關卡**：新集 `content_vec` vs 該使用者歷史集 cosine，**超門檻就退回重生**（self-refine：把「太像第 N 集，請換 X 角度」當回饋塞回 prompt）。

**【成本/可靠度修正】重試必須硬上限**：2026-06-15 後重生重試**不再是免費訂閱算力**，是扣 API 費用。所以：
- 重試上限 2–3 次，到頂就降級為換 angle 重排或人工 review。
- 自我抄襲門檻**寧可漏抓也別誤殺**——誤殺會觸發無限重生風暴、打爆預算。
- 角度耗盡 fallback（同狹窄主題 6 角度用罄）：擴大聚類半徑引鄰近題材、或降級為**明示的複習集**（不假裝是新內容）。

---

## 7. 核心機制三：重用決策函式 + 新鮮度

把重用寫成單一純函式，三條件全 AND，缺一不可重用：

```
should_reuse(candidate, request) =
      語意相似度(candidate.topic_vec, request) ≥ 類別門檻
  AND now < candidate.expires_at                 # 未過期
  AND candidate.id ∉ request.user.heard_set      # 他沒聽過（硬條件）
```

**新鮮度是「沉默失效模式」的解藥**：語意相似度沒有時間概念——18 個月前的舊集若語意吻合，cosine 分數和昨天的一樣高，retriever 不知道它過期了。所以 `freshness_class` 必須在生成時就寫成一等欄位：

| freshness_class | 範例 | TTL / 重用策略 |
|---|---|---|
| `news` / timely | 「本週 AI 新聞」「最新 iPhone」 | 1–7 天，過期**必重生**，過期集不可重用 |
| `product` | 某產品版本解說 | 2–4 週 |
| `evergreen` / skill | 「光合作用」「現在完成式」「旅遊英語」 | 數月至無限，但仍受 novelty 限制 |

> `topic_type` 分類**錯誤代價不對稱**：把 news 標成 evergreen → 把過期時事重發 → 嚴重感知問題。所以**不確定一律保守標 fast-decay**。重用查詢直接用 metadata filter（`now < expires_at`）濾掉過期集，避免「先撈再丟」。

---

## 7.5 核心機制四：點擊查字與單字庫（已拍板 2026-06-16）

> **決策摘要**：採「**ECDICT 客戶端離線首查 + CMUdict 補 IPA + Piper TTS 自合音檔 + 單字本 server-side 綁 user_id**」組合。**避開** Wiktionary / CC-CEDICT 兩大授權地雷（CC-BY-SA 4.0 + GFDL，share-alike 對閉源商業產品是禁區）。點擊只做 **word-level**（不拆 subword），呈現走「**側邊欄 drawer + dismissible 浮動卡備援**」，打不斷播放。

### 7.5.1 為何選這套（vs 業界各家方案）

| 層 | 選擇 | 授權 | 規模 | 為什麼 |
|---|---|---|---|---|
| **主字典** | **ECDICT** | MIT | 77 萬條、SQLite ~20 MB | 唯一同時滿足：MIT 商用可閉源 / 中英對照 / 結構化 / 離線塞前端。WordNet 無中文、FreeDict 偏技術詞、GPL 不友善；Wiktionary 雖資料最豐富但 share-alike 不行 |
| **補 IPA** | **CMUdict** | 公領域 / BSD | 13.4 萬詞 | ARPAbet→IPA 需寫一行轉換（`cmudict` PyPI 套件內建）；ECDICT 自帶 IPA 覆蓋率非 100%，CMUdict 補齊 |
| **補音檔** | **Piper TTS** | MIT、ONNX | 自合 | **沒有可商用的「現成真人單字音檔庫」**——Forvo 2019 改授權變非商業、Common Voice 是語句級、LibriVox 是書。Piper MIT 可離線自合、模型幾十 MB |
| **同/反義詞**（日後）| WordNet | BSD | 16 萬 | MVP 不用；要時再接，英文 sense 對到 ECDICT 中文翻譯即可 |
| **不用 Wiktionary dump** | — | CC-BY-SA 4.0 + GFDL | 750 萬 | **share-alike ＝ 衍生內容須同授權散布，塞閉源商業產品即違規**。唯一解套是「字典層以 CC-BY-SA 標示散布、產品其餘閉源」分開，但增加法務成本與使用者困惑 |
| **不用 CC-CEDICT** | — | CC-BY-SA 4.0 | 12.5 萬 | 不只 share-alike，**方向還是反的**（中→英） |
| **不用 FreeDict en-zh** | — | GPL | 偏少 | GPL 對閉源產品同樣是 copyleft 禁區；en-zh 收詞偏少 |

### 7.5.2 互動 UX（業界共通模式 + 對 DawnCast 的選擇）

| 面向 | 選擇 | 理由 |
|---|---|---|
| **觸發** | 點擊字幕 word token（單擊）| Voscreen / LingQ / Language Reactor 共通模式；長按 / 雙擊易誤觸、hover 行動裝置不支援 |
| **呈現** | 側邊欄 drawer 為主，dismissible 浮動卡為備援 | podcast 場景不能全屏 modal（打斷播放）；側欄一邊聽一邊看；浮動卡給手機用 |
| **拆解粒度** | **word-level**，不拆 subword | 實務 app 沒人拆 morphology；增加複雜度邊際效益低 |
| **「don't show this again」陷阱** | 設定頁必有「重置 popup 偏好」開關 | LingQ 論壇有用戶誤勾後找不到怎麼開 |
| **高亮已存單字** | 字幕中已存單字持續高亮（視覺記憶錨） | Language Reactor / LingQ 共通設計 |
| **詞形變化** | 查原型 + 顯示 ECDICT `exchange` 欄位 | ECDICT 自帶過去式/複數/比較級欄位，零成本 |
| **跨字詞組** | 拖曳選多字查 phrase | LingQ 經典設計；對 collocation / 慣用語有用 |

### 7.5.3 資料流（點擊查字 → 收進單字本）

```
[播放頁] 點擊 word token
   ↓
[前端] 1. 查 ECDICT SQLite bundle（離線，0 延遲，常見 5K 詞覆蓋率 ~85%）
   ↓ 命中 → 顯示詞卡
   ↓ 未命中
[後端 GET /api/dict/lookup?w=xxx]
   ↓ 2. 線上 fallback（自架 LLM 翻譯 + 補 IPA/例句；或預載 WordNet 翻譯對照表）
   ↓ 命中 → 回傳並快取到 client bundle（lazy 補進去）
   ↓
[使用者按「加入單字本」]
   ↓
[後端 POST /api/vocab {user_id, word, lemma, sense, source_episode_id, source_line_no}]
   ↓
[DB] 寫入 user_vocab（綁 user_id，不寫 localStorage；可同步、可匯出）
```

> **為何單字本走 server-side**：LingQ 17 年老牌、Voscreen 訂閱制都走雲端帳號；localStorage 沒跨裝置 = 失去 SaaS 核心價值。DawnCast 即使 MVP 不做訂閱，**也應把 user_vocab 綁 user_id 設計做好**（介面化預留）。

### 7.5.4 資料模型補一張表

```sql
-- 7.5.4 user_vocab（單字本）
CREATE TABLE user_vocab (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id       uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  word          text NOT NULL,         -- 使用者點擊的字（含大小寫還原）
  lemma         text NOT NULL,         -- 詞形還原後原型（ECDICT exchange 推導）
  pos           text,                  -- 詞性（n./v./adj.）
  translation   text NOT NULL,         -- 中文翻譯（ECDICT 或線上 fallback）
  ipa           text,                  -- IPA 音標
  sense_idx     smallint DEFAULT 0,    -- 同字多義的第幾義
  source_episode_id uuid REFERENCES episodes(id) ON DELETE SET NULL,
  source_line_no    int,               -- 從哪一行點的
  status        smallint NOT NULL DEFAULT 1,  -- 1=new 2=learning 3=familiar 4=learned 5=ignored
  next_review_at timestamptz,          -- SRS 排程
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (user_id, word, sense_idx)
);

CREATE INDEX idx_user_vocab_user    ON user_vocab (user_id, status);
CREATE INDEX idx_user_vocab_review  ON user_vocab (user_id, next_review_at) WHERE status BETWEEN 1 AND 3;
```

> **MVP 暫不做 SRS 演算法**（先存、顯示、匯出），`next_review_at` 欄位先留好介面；V1.1 接入 FSRS 或 SM-2。

### 7.5.5 MVP 取捨

- **做**：ECDICT bundle 進前端、點擊查 word-level 詞卡、加入單字本（綁 user_id）、已存單字字幕高亮、設定頁重置 popup 開關
- **不做**（先留介面）：SRS 演算法、phrase 拖曳查、線上 fallback（先一律走 ECDICT）、同/反義詞、Piper TTS 自合音檔（先走 TTS API lazy 載入即可）
- **不做**（明確排除）：subword 拆解、英文 Wiktionary dump、Wikidata Lexemes SPARQL

> **授權合規紀律**：ECDICT 與 Piper TTS 都是 MIT（含「商用、可改、可閉源、保留著作權聲明」四項義務——bundle 內附 `THIRD_PARTY_LICENSES` 與原作者 credit 即可）。CMUdict 公領域/BSD 雙重保護，標示更簡。**Wiktionary / CC-CEDICT / FreeDict 三者一率不碰**。

---

## 8. 生成引擎策略 ✅ 已拍板（2026-06-16）

> **決策（Alan 拍板）**：採「**訂閱 MiniMax Coding Plan + OpenClaw 當主引擎、`api_key` adapter 為可逆 fallback**」。你正確指出串接 OpenClaw（小龍蝦）官方支援、吃訂閱定額、能省按量費——對抗式驗證確認機制屬實。先前 §8 寫的「明文禁 batch + 高封號 + 存亡級」過火且錯誤（見校正聲明），已刪除。**失敗模式**：撞 5h/週限流窗時當晚批次降級成 §5 的 evergreen 常青集（degrade，非斷線），adapter 隨時可一鍵切到 `api_key`。下方三條柱子是「為何仍保留 adapter 可逆、不寫死訂閱唯一」的理由——你已知悉並接受。

> **本決策的合規義務（接受訂閱主引擎的前提）**：
> 1. **adapter 必須可逆**：`api_key` 實作與 `minimax` 並存、一鍵可切（不可寫死只有 `minimax`）。
> 2. **訂閱 key 只在你們夫妻帳號內用**：不池化、不分享、不轉售 access——避免踩「轉售/散布服務」真禁令。
> 3. **落地前實機核對 ToS**：瀏覽器開 `platform.minimax.io` 逐字核「規避費用 / 限流」條文（研究全是搜尋擷取）。
> 4. **批次控在限流容忍內**：單帳號、避開尖峰（工作日 15:00–17:30）、跑凌晨；撞窗即降級 evergreen。
> 5. **規模/營收成長到痛點即切 `api_key`**：屆時按量 API 反而更便宜、零灰色、線性擴容。

> **校正聲明（被打臉處）**：本節 V1.0 曾寫「MiniMax Coding Plan ToS 明文禁止 batch / custom backend」「直接違約」「封號 = 存亡級風險」。專責查 ToS 的對抗式驗證 high confidence 否定：MiniMax 條款**沒有一條**寫「僅限互動式開發、禁止後端自動化」，也無「personal use only」明文；batch/multi-user 字眼只出現在 **fair-use 動態限流**語境（節流，非授權禁令）。此段已依查證重寫。

### 8.1 串接機制屬實 + 真正咬得住的條款（驗證證據）

| 事實 | 來源時間 | 對 DawnCast 的意義 |
|---|---|---|
| 「小龍蝦」= **OpenClaw**（開源 agent，logo 是紅色小龍蝦 Molty），在 MiniMax 官方「10+ 已適配工具」清單內；串接是把 `ANTHROPIC_BASE_URL` 指到 MiniMax `/anthropic` endpoint、`AUTH_TOKEN` 塞訂閱 key（OpenClaw 走 OAuth），**吃訂閱定額、不走 PAYG 計費** | 2026 | **你說的機制為真**——串接官方支援、「省按量費」技術上成立，不是幻想 |
| Claude Code headless（`claude -p`）/ Agent SDK 用量**退出訂閱定額**，改抽獨立月度 credit、**按標準 API 價計費、不滾存、用完即停** | 2026-06-15 | 「訂閱即便宜無限」對自動化批次已**不成立**——Claude headless 本質已 API 化 |
| Agent SDK **明文要求 API key**，Free/Pro/Max 的 OAuth token 不可用於自動化 | 2026-02 | 「用訂閱 OAuth 跑 SDK 當引擎」這條路**被封死** |
| **真正咬字面的禁令**：MiniMax/Anthropic 條款均禁「access services in a manner that **circumvents fees or otherwise evades usage restrictions**」 | 2026 | DawnCast 動機「省按量費」＝把本該走 PAYG 的對外 production 批次塞進個人訂閱定額軌，定性落在此條——**與會不會被抓無關，是性質問題** |
| MiniMax 官方定位「面向個人開發者的**交互式**使用場景」「**生產環境建議使用按量付費**」；超高並發批次/多用戶共享列為**動態限流**對象 | 2026 | 這是定位/建議+限流，**非明文禁令**；但 DawnCast 形態（cron 無人值守、多用戶）正是被節流的目標 |
| 供應商政策**主動收緊**：Anthropic 2026-01 封 OAuth、02 改 ToS、**04-04 對含 OpenClaw 的第三方 agent 斷供訂閱額度**、06-15 改獨立計量信用池 | 2026-01→06 | 「訂閱吃到飽跑批次」的經濟前提正被供應商**單方拆除**——趨勢風險，不需 MiniMax 封號實證即成立 |
| MiniMax 當家模型 M2.7 已從 MIT 改為**限制商用的 Modified-MIT**，自架商用需書面授權 | 2026-03-18 | 沒有合法自架逃生艙——舊弱版 M2 才是 MIT，但品質差一截、夫妻團隊買不起 4×H200 |

> **Live ToS 核對 caveat**：MiniMax Platform ToS / Token Plan FAQ 全文為 JS 動態渲染，上述「規避費用」「限流」逐字條文取自搜尋引擎索引（跨多次查詢一致），**未 100% 逐字核對標點與條號**。法務定論前須用瀏覽器實機開啟 `platform.minimax.io` 官方頁逐字核對；勿引用第三方鏡像站。

**結論**：DawnCast 用訂閱當無人值守唯一後端，**省的錢微不足道（單集文本 $0.005–0.014）、卻把引擎穩定性押在一條規避費用禁令的字面 + 一個正被供應商主動拆除的計費前提上**。三支柱：①規避費用條款定性、②供應商趨勢收緊、③省的錢不值得。本質仍是「按量 API 依賴」最穩。

### 8.2 好消息：合規路徑反而**更便宜**

LLM 寫稿是成本零頭——單集約 3K input + 1–3.5K output token：

| 引擎 | 單集 LLM 成本 | N=10,000 全月 LLM |
|---|---|---|
| **MiniMax M2.5 API**（最便宜合規主力） | ~NT$0.05 | ~$12 |
| Claude Sonnet 4.6 API | ~NT$0.7 | — |
| Claude Opus 4.8 API（留審稿/敏感主題） | ~NT$1.2 | 全壓會爆額度 |

走合規按量 API 不但**零 ToS 風險**，成本可能**比 $20 訂閱還低**，且可線性擴容。

### 8.3 本 PRD 採取的設計（已拍板：訂閱主引擎 + 可逆 fallback）

把生成引擎抽象成 **`GenerationEngine` adapter**（`minimax` / `api_key` / `claude_code` 三實作同一介面，同一份 prompt 與 script JSON 契約）：

- **主引擎 `minimax`**：透過 OpenClaw（或任一 OpenAI/Anthropic 相容客戶端）把 `ANTHROPIC_BASE_URL` 指向 MiniMax `/anthropic` endpoint、`AUTH_TOKEN` 塞訂閱 token，吃訂閱定額池寫稿。零邊際成本（你們已付的訂閱）。
- **可逆 fallback `api_key`**：MiniMax M2.5 / Anthropic 按量 API，與主引擎同介面。**限流/額度撞窗時的失效轉移策略（二選一，做成 config）**：
  - **(a) degrade**：當晚批次切 §5 evergreen 常青集（免費、不新鮮）——預設。
  - **(b) failover**：當晚臨時切 `api_key` 跑完（小成本、保新鮮）——營收期可開。
- **`claude_code` / 互動式 TUI 只供你們夫妻開發期手動用**：劇本品質審核、prompt 調校、難主題人工把關，**絕不接進 always-on 批次**。
- **可逆性是硬約束**：adapter 工廠以一個環境變數/設定切換引擎，切換不需改 worker 程式碼。供應商政策變動（如 Anthropic 04-04 對 OpenClaw 斷供那種）時，一鍵切 `api_key` 保黎明交付不中斷。

> **殘留風險（已採可逆設計，仍須知悉並監控）**：訂閱主引擎帶三個必須盯著的點——(1) 動機「省按量費」把對外 production 批次塞進個人訂閱定額軌，定性落在「規避費用 / 規避用量限制」條款字面（與會不會被抓無關，是性質問題）；(2) Claude headless 已 API 化、Anthropic 04-04 已對 OpenClaw 斷供，MiniMax 端最可能後果是**尖峰動態限流**（非封號實證，但供應商趨勢在收緊、計費前提由對方單方控制）；(3) 撞到 5 小時/每週窗口上限當晚批次受影響。**這三點都被本設計兜住**：§5 的 03:30 evergreen 兜底把黎明 SLA 與生成成功率解耦（限流→degrade 成常青集，非 outage），adapter 可逆讓計費前提被拆時一鍵切 `api_key`。**監控指標**：訂閱剩餘配額 %、限流命中率、degrade 觸發次數——任一惡化即評估切 `api_key`。

### 8.4 TTS：MVP 維持 edge-tts
- TTS 才是真正成本瓶頸：edge-tts **免費且已驗證可運作**；MiniMax Speech-02-Turbo 單集 ~700 字 ≈ 4000 字元 ≈ **NT$7.5**，幾乎吃光整個 NT$3–8 預算。
- edge-tts 是非官方逆向 API，有被 Microsoft 切斷風險 → 監控可用性、預留替代 TTS adapter。
- Speech-02 列為 **V1.1 付費方案的 A/B 候選**：付費音質升級**必須先用真實盲測**（含雙人對話跨段音色一致性）證明值得才上。

---

## 9. 成本模型（修正後）

**成本槓桿不在 LLM、也不在去重，而在 TTS 與運算/儲存。**

### 9.1 單集邊際成本（MVP，edge-tts 免費）
```
LLM 寫稿 NT$0.05–1.2 + 嵌入 <NT$0.01 + TTS NT$0 + R2 寫入/儲存 ~NT$0.05
≈ 單集真實邊際成本 NT$1–2（遠低於原估 NT$3–8，對團隊有利）
```

### 9.2 去重節省（吸收驗證修正）
84% / 95% / 98% 的節省**數字成立且對重用率不敏感**——但成立原因是 **cluster collapse + 8 個大方向共享集**，不是向量重用、也不是省 LLM。即使向量重用率設 0%，N=10,000 仍省約 94%。

| 訂閱數 N | 每晚實際新生成集數 | 相對「一人一集」節省 |
|---|---|---|
| 100 | ~16 集 | ~84% |
| 1,000 | ~49 集 | ~95% |
| 10,000 | ~188 集 | ~98% |

> 假設：30% 指定主題、70% 走大方向（最多 8 共享集）；指定主題經聚類塌縮。

### 9.3 引擎額度 vs 成本
- **MiniMax 按量 API**：N=10,000 全月 LLM ≈ $12，最便宜合規主力。
- **Claude Agent SDK 信用制**（2026-06-15 起）：Max20x $200/月、用完即停。N=10,000 全壓 Opus 月約 $480 會爆額度 → 高 N 須分流 MiniMax/Sonnet，Claude 只留審稿。
- **R2 儲存**：單集 ≈ 10.5MB（mp3 3.8 + mp4 6.7）；$0.015/GB、egress 免費。一年累積 N=100/1,000/10,000 約 $1.4 / $4.2 / $16/月。

### 9.4 月度總成本（MVP, N≈100）
```
Supabase Free/Pro $0–25 + Worker 容器 ~$10 + R2 ~$1.5 + LLM/嵌入 <$5
≈ $15–40/月
```

---

## 10. 關鍵技術決策表

| 決策 | 建議 | 理由摘要 | 否決的替代方案 |
|---|---|---|---|
| 生成引擎 | **訂閱+OpenClaw 主引擎 + `api_key` 可逆 fallback**（Alan 拍板 2026-06-16）；adapter 抽象；`claude_code` 僅夫妻手動用 | 訂閱零邊際成本（已付）；機制官方支援；限流→degrade evergreen、計費前提被拆→一鍵切 api_key，風險被兜住且可逆 | 訂閱當**唯一**引擎、無 adapter（不可逆，計費前提被單方拆時無退路）；自架 M2.7（需書面授權+硬體買不起）；舊弱 M2 自架（品質差） |
| 向量資料庫 | Supabase pgvector，起步免索引精確掃描 | 數千–上萬筆、2 人、已用 Supabase，同庫同備份最無聊正確；免費層 32MB 建 HNSW 會失敗 | Pinecone/Qdrant/Weaviate/Chroma（優勢要數百萬筆才顯現） |
| 嵌入模型 | 雲端 API（Gemini / OpenAI text-embedding-3），中英 canonical 字串嵌入 | 月用量遠低於自架損益點；別過度迷信單一供應商跨語榜分數，用真實樣本實測 | 自架 BGE-M3（維運>省下費用，延後）；純英文/弱多語（跨語崩） |
| 去重門檻 | 三階段漏斗 + LLM 正規化前處理；門檻用真實樣本離線校準，跨語/同語分別定 | **0.85 對 3-small 是錯的**（不相關平均 ~0.43），正確 0.40–0.55；前處理比調門檻可靠 | 單一全域固定門檻；純嵌入無 LLM 正規化 |
| novelty | deliveries anti-join + content_vec + variant_no；固定 6 角度強制輪替；自我抄襲關卡重試硬上限 | 純 regenerate 會同質化；2026-06-15 後重試扣 API 費用，必須硬上限防重生風暴 | LLM 自己想角度；無上限自我抄襲偵測；語義噪音底線三階段（過度工程） |
| 排程 | Supabase pg_cron 主排程，只負責 tick；重活在常駐 worker；GitHub Actions 僅備援 | GitHub Actions cron 延遲/丟棄/60 天自動停用，違反黎明 SLA；Edge Functions 跑不了 ffmpeg | GitHub Actions 當主排程；Edge Functions 跑 ffmpeg |
| TTS | MVP 維持 edge-tts；MiniMax Speech-02 列 V1.1 A/B 候選 | edge-tts 免費已驗證；Speech-02 單集 ~NT$7.5 吃光預算，升級需盲測證明 | 全面換 Speech-02（爆預算 + 一致性未驗證） |

---

## 11. MVP 範圍

### 11.1 In Scope
- [ ] **修 POC 契約缺口**（第一個硬阻塞）：script JSON 每行加 `zh`，`generate_subtitles.py` 改讀 `script[i].zh`、刪 `TRANSLATIONS` dict 與 `scripts_en.json` mirror
- [ ] `GenerationEngine` adapter：主引擎 `minimax`（OpenClaw 訂閱）+ `api_key` 可逆 fallback（同介面、env 一鍵切）一次產出含逐行 `zh` 的 script JSON
- [ ] Supabase 6 張核心表 + pgvector（起步免索引、精確掃描，100% recall）
- [ ] 夜間批次：pg_cron tick + 一台常駐 worker（pgmq 或 jobs+SKIP LOCKED）跑既有 `generate_episode.py` / `generate_subtitles.py`
- [ ] 主題正規化（LLM canonical + topic_type）→ 確定性合併 → cosine 連通分量聚類
- [ ] **大方向共享集**（未指定且大方向相同者共用同一集——最省、最先做）
- [ ] **heard-set 防重發**（deliveries anti-join）
- [ ] `freshness_class` 驅動重用 TTL，timely 過期不可重用
- [ ] **evergreen 兜底池 + 03:30 補發掃描**（保證每個訂閱者黎明一定有東西聽）
- [ ] R2 簽章交付 + owner 授權檢查；RSS / 簡單 Web 收聽頁
- [ ] edge-tts 維持為唯一 TTS
- [ ] **點擊查字 MVP**（§7.5）：ECDICT bundle 塞前端、word-level 詞卡側欄 drawer、加入單字本（綁 user_id）、已存單字字幕高亮、設定頁重置 popup 開關
- [ ] 上線前用真實中英主題對**離線校準聚類門檻**（跨語/同語分別）

### 11.2 Out of Scope（明確不做）
- 訂閱制當對外生產**唯一**後端（踩規避費用條款 + Claude headless 已 API 化 + 供應商趨勢收緊；註：串接 OpenClaw 等省按量費機制成立，但仍排除在 always-on 批次外）
- 向量庫「跨使用者語意重用別人生成的集」（小規模 ROI 不成立，延 V2）
- MiniMax Speech-02 取代 edge-tts（爆預算，需 A/B 證明）
- HNSW 索引（數萬筆以下精確掃描即可，且免費層建 HNSW 會失敗）
- HDBSCAN / UMAP 聚類（過度工程）
- Bloom filter heard-set（小規模 NOT IN 即可，介面化預留）
- 自我抄襲「語義噪音底線」三階段量測（過度工程，MVP 用固定門檻 + 人工抽聽 1 集/週）
- 原生 App、金流、A2–B2 動態分級、SRS 演算法 / 學習進度（V1.1+）
- 點擊查字延伸：phrase 拖曳查、線上 fallback、subword 拆解、Piper TTS 自合音檔、同/反義詞（V1.1+）
- 多時區精細排程優化（MVP 先單一交付窗）

---

## 12. 分期路線

### MVP — 出貨：無人值守每晚產出並黎明交付
**目標**：把既有 POC 接成「預約→夜間批次→黎明交付」最小可運作閉環，驗證「大方向共享集省成本」與「heard-set 防重發」。
交付項見 §11.1。

### V1.1 — 品質與留存
- novelty 引擎：固定 6 角度 taxonomy + content_vec 自我抄襲關卡（重試硬上限）
- `freshness_class` TTL + timely 過期重生
- 推播/RSS 晨間回訪鉤子（morning routine 習慣養成）
- TTS A/B：MiniMax Speech-02 vs edge-tts 盲測
- 生字本/逐行對照 UI、completion 追蹤（`deliveries.position_sec`）
- 門檻監控與分類別校準迴圈（累積 50–100 標註對）

### V2 — 規模化與護城河
- 向量庫「重用別人生成、他沒聽過的集」（請求量達數百/晚、ROI 翻正才啟用）
- HNSW 索引（向量數逼近數十萬時）+ Supabase Pro
- heard-set 介面換 Bloom filter（使用者破萬）
- A2–B2 動態分級引擎（對標 CEFR 偵測反向做生成）
- 多時區精細排程 + 多 adapter 自動降載/切換
- 重用率、單使用者邊際成本 dashboard
- 台灣在地主題分布資料護城河累積

---

## 13. 風險與緩解

| # | 風險 | 嚴重度 | 緩解 |
|---|---|---|---|
| R1 | **訂閱引擎計費前提崩塌 + 條款定性**：動機「省按量費」把對外批次塞訂閱定額軌、踩「規避費用」條款；Claude headless 已 API 化、04-04 對 OpenClaw 斷供；供應商趨勢主動收緊（MiniMax 端最可能為尖峰限流，非封號實證）。註：MiniMax **無**明文禁 batch，先前 R1 寫「禁 batch/封號/致命」已校正 | 中（被 03:30 evergreen 兜底降載，非存亡級） | adapter 抽象、主引擎訂閱+OpenClaw、`api_key` 可逆 fallback；訂閱僅夫妻帳號內用；可靠度靠 adapter 一鍵切 + degrade/failover 二選一 + evergreen 兜底（引擎被限流→當晚降級常青集，不斷線）；監控配額%/限流命中/degrade 次數 |
| R2 | **跨語去重靜默漏判**：3-small 不相關平均 ~0.43，0.85 門檻讓幾乎所有東西不被聚類；跨語短詞對齊系統性偏低，漏判無 error/log，只在帳單出血 | 高 | 嵌入前 LLM 正規化成中英雙語 canonical；門檻用真實對離線校準（0.40–0.55），跨語/同語分別定；輔以別名表；絕不拍腦袋用 0.85 |
| R3 | **POC 翻譯硬編碼未修**→無人值守跑不起來，每集需人工補翻譯，吃掉「極省人力」核心優勢 | 高 | MVP 第一個任務就修：script 每行 `{speaker,text,zh}`，刪 TRANSLATIONS |
| R4 | **過度去重的「回收感」與信任流失**：70% 共用 8 個大方向集，小眾使用者長期被分到別人的集，satiation 與 distrust | 中 | 同使用者永遠走 novelty 換角度，只在跨使用者間共用；對外只呈現「今日主題」不暴露「集編號/重用」；per-user 多樣性上限 |
| R5 | **TTS 音質/成本兩難 + edge-tts 斷線**：edge-tts 是非官方逆向 API；換 Speech-02 單集 ~NT$7.5 吃光預算且一致性未驗證 | 中 | MVP 維持 edge-tts 並監控、預留替代 adapter；Speech-02 列 V1.1 A/B，升級須盲測證明 |
| R6 | **單台 worker 單點故障 + 黎明 SLA** | 中 | worker 帶 health check 自動重啟；pg_cron 為主排程；03:30 兜底掃描把未交付者全補 evergreen，SLA 與生成成功率解耦 |
| R7 | **MiniMax 單一中國供應商地緣/資料合規**：預約主題（含個人偏好）送進中國 API，2017 國家情報法/PIPL 跨境審查，認證未經驗證 | 中 | adapter 保留切換到非中國供應商；敏感/可識別主題路由到非中國 API；行銷透明揭露資料流向 |
| R8 | **點擊查字授權地雷踩雷**：誤用 Wiktionary dump / CC-CEDICT / FreeDict en-zh（皆 CC-BY-SA / GPL）塞進閉源商業產品 = 衍生內容須同授權散布 = 閉源失效 | 中（已選 ECDICT+CMUdict+Piper 全 MIT/公領域，預設安全）| 採 §7.5 授權矩陣，bundle 內附 `THIRD_PARTY_LICENSES` 與原作者 credit；CI 加授權掃描（dep 與資料集皆查）；擴字典前先翻 `LICENSE` 檔，禁用任何 share-alike 來源 |
| R9 | **單字本綁 localStorage 失去跨裝置價值**：誤把 user_vocab 寫 client 端，SaaS 核心差異化消失 | 中 | schema 直接走 server-side `user_vocab`（§7.5.4），前端 SDK 只讀寫 API、不直接存 IndexedDB；介面化預留匯出 Anki |

---

## 14. 上線前必做的校準與未解問題

**必做（資料驅動，不能拍腦袋）**：
1. **聚類/重用門檻校準**：蒐集一批台灣使用者真實中英主題輸入，人工標註「該不該合併」50–100 對，掃 cosine 取 F1 最大；跨語對與同語對分別定門檻。
2. **嵌入模型選型實測**：用 DawnCast 真實主題樣本對 Gemini / OpenAI 各跑一輪跨語去重評測再定（別只看單一供應商榜單）。
3. **content_vec 嵌什麼**：extracted_facts vs 整段腳本摘要 vs 組合，哪個對 novelty 判定最準，用真實多集腳本離線測。
4. **單集真實 token 成本實測**：回推訂閱層/API 的每晚新生成上限與訂閱者天花板。

**待產品決策**：
- onboarding「大方向主題」的粒度與數量（直接決定共享集數與內容單一化風險）。
- 角度耗盡時的 fallback（擴大聚類半徑 / 升難度 / 明示複習集 / 建議換主題）。
- `heard` 記「已交付」還是「實際聽完」（影響 novelty 嚴格度）。
- 預約截止與黎明交付的精確 SLA（決定夜間窗長度與 evergreen 池大小）。
- §8 的引擎策略最終拍板。

---

## 15. 成功指標（KPI）

| 類別 | 指標 | 目標/用途 |
|---|---|---|
| 引擎健康 | 黎明交付準時率 | > 99%（含 evergreen 兜底） |
| 成本 | 單使用者邊際成本 | MVP 監控，驗證去重槓桿 |
| 去重 | 每晚新生成集數 / 訂閱數 | 驗證 cluster collapse 省成本 |
| 去重品質 | 誤重用率（使用者跳過/負評代理指標） | 監控門檻是否漂移 |
| novelty | 同使用者同主題重複感（抽聽/負評） | 驗證角度輪替有效 |
| 留存 | 完播率 / 晨間回訪率 | 對齊 master plan KPI（完播 > 75%） |

---

## 附錄 A：研究方法與信心標註

本 PRD 由 7 維度平行研究（engine / vectordb / dedup / novelty / competitive / pipeline / datamodel）→ 對抗式驗證 → 綜合產生（15 agents、約 128 萬 token）。

**對抗式驗證推翻/降信心的主要主張**（已吸收進本文）：
- 「訂閱當**唯一**引擎、無退路、MIT 自架兜底」安全網對現役模型已失效（M2.7 Modified-MIT、Claude headless 2026-06-15 API 化）→ **改為訂閱+OpenClaw 主引擎 + `api_key` 可逆 fallback（adapter 一鍵切）**。
- cosine 0.85 去重門檻對 text-embedding-3-small **方向性錯誤**（不相關平均 ~0.43）→ **改 0.40–0.55 + 真實校準**。
- 去重「省 LLM 成本」是錯置歸因（LLM 已是零頭）→ **重新定義為省 TTS/運算 + 維持 novelty**。
- 「排程化每日遞送是市場空白」過度樂觀（Spotify Studio 2026-05 已做 daily briefing）→ 護城河重押**台灣在地教學 + 去重成本工程 + 累積資料**。
- pipeline/datamodel 的無聊架構（pg_cron + 常駐 worker + pgmq SKIP LOCKED + 冪等鍵 + zh 移進 script JSON）**驗證紮實，照單採用**。

**競品定位提醒**：通用 AI podcast 生成（NotebookLM / Spotify Studio / Alexa+）已紅海，「指定主題→雙人對話」是 commodity。DawnCast 真正利基是「**個人化主題 + 固定 B1 英語學習格式 + 雙語字幕 + 夜間預約晨間遞送 + 台灣在地化**」這個組合，護城河中等偏弱、格式易被複製，須靠累積台灣使用者真實主題分布與學習數據下沉護城河。

---

**版本歷史**
- v1.0（2026-06-16）：初稿。聚焦個人化生成引擎，基於 7 維度研究 + 對抗式驗證。
- v1.1（2026-06-16）：Alan 反駁「MiniMax 可串接 OpenClaw（小龍蝦）」觸發第二輪對抗式查證。**校正 §8 兩處過火**（MiniMax 無明文禁 batch、封號非存亡級），改掛「規避費用條款 + 趨勢收緊 + 省的錢微不足道」三柱。**引擎策略拍板＝訂閱+OpenClaw 主引擎 + `api_key` 可逆 fallback**（限流→degrade evergreen；計費前提被拆→一鍵切 api_key）。
