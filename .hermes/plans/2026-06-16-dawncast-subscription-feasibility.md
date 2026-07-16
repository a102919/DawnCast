---
project: DawnCast
doc_type: satellite_research
title: DawnCast 訂閱制可行性研究
date: 2026-06-16
status: draft
version: 0.1
---

# DawnCast 訂閱制可行性研究

> 目的：評估把 DawnCast（AI 自動生成英語 podcast）做成訂閱商品是否成立。
> 這是衛星研究文件，整理所有變數與待決定項目。
> Alan 看過後，再決定哪些要進 V1.0 master plan。

---

## 1. 產品定位：雙重身份

DawnCast 已經證明可以做出一集合格的 B1 等級英語 podcast（mp3 + 雙語字幕 mp4 + 結構化字幕）。這個技術底座可以服務**兩個完全不同的市場**，定價、毛利、客戶旅程都不同：

| 面向 | B2C：英語學習者 | B2B：內容自動化 |
|------|---------------|----------------|
| 客戶 | 個人訂閱者 | podcast 業者、語言學校、行銷團隊 |
| 交付 | 每日一集音檔 + 字幕 | 自動化 API / 排程生成 |
| 價值主張 | 通勤學英語，每日 3 分鐘 | 不用錄音室也能日更 podcast |
| 單價 | NT$149-299/月 | NT$2-15/集，月繳大量 |
| 取得成本 | 內容行銷 / SEO / KOL | B2B 業務 / LinkedIn |
| 毛利 | 中（人工審稿成本） | 高（全自動化） |

**關鍵決策**：先做 B2C 還是 B2B？
- B2C 門檻低、品牌累積快、但 CAC 高
- B2B 變現快、但需要 demo + 業務人力
- **建議**：先 B2C 養品牌，B2B 客戶會主動找上門（看 references/middleman-business-patterns.md 的 middleman 哲學，數位商品可以零庫存啟動）

---

## 2. 內容生產成本（已驗證）

實際跑過一集的資源消耗：

| 項目 | 成本/集 | 備註 |
|------|--------|------|
| edge-tts（Alex/Sarah 語音） | NT$0 | 微軟免費 API |
| 字幕生成 script（Python） | NT$0 | 一次性開發成本 |
| ffmpeg 燒字幕 | NT$0 | 本地執行 |
| LLM 寫稿（GPT-4o-mini） | NT$1-3 | ~2000 tokens |
| LLM 事實查核（perplexity/sonar） | NT$2-5 | 視 topic |
| 人工審稿（Alan 30 min） | NT$150* | 機會成本 |
| **總計（自動化部分）** | **NT$3-8/集** | 可降到 NT$0 用免費模型 |
| **總計（含人工）** | **NT$153-158/集** | |

*以 Alan 時薪 NT$300 估計

**毛利槓桿點**：
- 自動化腳本寫好後，**人工審稿可降到 5 分鐘**（檢查敏感詞 + 確認收尾）
- 寫稿用 Claude/本地模型可壓到 NT$0.5/集
- **目標**：日更 1 集，自動化成本 NT$5 以內

---

## 3. 定價層規劃（B2C 為主）

參考台灣英語學習訂閱市場現況：

| 訂閱層 | 月費 | 內容 | 目標客戶 |
|--------|------|------|---------|
| Free | NT$0 | 每日 1 集含廣告、3 秒片頭廣告 | 試聽用 |
| Basic | NT$149 | 每日 1 集無廣告、雙語字幕下載 | 個人學習者 |
| Pro | NT$299 | 全部集數 + 學習筆記 PDF + 單字卡 quiz | 認真學的人 |
| 年訂閱 | NT$2,490（平均 NT$208/月） | Pro 等級 | 8 折鼓勵年繳 |
| 教育授權 | NT$4,990/班/月 | 全校授權 + 客製 topic + 學習單 | 補習班、安親班 |

**年收入模型（保守估）**：

| 月份 | Basic | Pro | 教育 | 月營收 | 累計訂閱者 |
|------|-------|-----|------|--------|-----------|
| M1 | 50 | 10 | 1 | NT$10,480 | 61 |
| M3 | 150 | 40 | 3 | NT$37,370 | 193 |
| M6 | 350 | 100 | 8 | NT$91,950 | 458 |
| M12 | 800 | 250 | 20 | NT$223,000 | 1,070 |

**假設**：
- 轉換率 Free → Basic：3-5%
- 流失率：月 8-12%
- 取得成本（CAC）：NT$150/人（內容行銷）

**LTV 估算**：
- 平均留存 6 個月 × 平均月費 NT$200 = **NT$1,200/人**
- CAC NT$150 → **LTV/CAC = 8x**（健康）

---

## 4. 競爭分析

| 競品 | 模式 | 月費 | 差異化 |
|------|------|------|--------|
| VoiceTube | 影片 + 字幕 | NT$149 | 真人影片，無音訊 podcast |
| Engoo | 一對一家教 | NT$1,990+ | 真人對話 |
| IELTS / TOEIC app | 考試導向 | NT$199-399 | 應試導向 |
| BBC Learning English | podcast | 免費 | 英文母語者非 B1 程度 |
| 6 Minute English (BBC) | podcast | 免費 | 程度過高、單向無對話 |
| **DawnCast** | **B1 雙人 podcast + 雙語字幕** | **NT$149** | **CEFR B1 量身打造、雙語字幕、自動化** |

**護城河**：
- CEFR B1 等級精準對齊（多數 podcast 是母語等級）
- 雙人對話（多數英語 podcast 是單人）
- **雙語字幕燒入 MP4**（少數平台有）
- 自動化生產 = 內容成本接近零

---

## 5. 技術堆疊成本（月費）

| 服務 | 用途 | 月費 |
|------|------|------|
| Supabase | 用戶資料 + 訂閱管理 + DB | 免費（< 500 MB） |
| Stripe / TapPay | 金流 | 抽成 2.8% |
| Cloudflare R2 | 音檔/影片 CDN | < NT$100 |
| Resend | email 通知 | 免費（< 100/天） |
| 網域 | dawncast.tw | NT$600/年 |
| LLM API（寫稿） | GPT-4o-mini | NT$500-1,500（視流量） |
| TTS | edge-tts | NT$0 |
| **總固定成本** | | **NT$600-1,700/月** |

**損益兩點**：
- 固定成本 ≈ NT$1,200/月
- 平均客單 NT$200 → 損益兩點 **6 個訂閱者**
- 100 個訂閱者時，毛利約 95%

---

## 6. 內容策略

**主題日曆（每週 5 集 + 2 集週末長版）**：

| 週一 | 週二 | 週三 | 週四 | 週五 | 週六 | 週日 |
|------|------|------|------|------|------|------|
| 科技 | 文化 | 商業 | 健康 | 環境 | 深度對談 (6 min) | 聽眾 QA |

**SEO/AEO 切入點**：
- 每集網頁含 transcript + 單字解釋
- 鎖定「B1 English podcast Taiwan」「英語 podcast 字幕」等長尾
- 結構化資料 FAQPage（已有 update_faq MCP tool）
- 短影音版：燒字幕的 mp4 拿去發 IG Reels / Shorts

---

## 7. 關鍵風險與緩解

| 風險 | 機率 | 影響 | 緩解 |
|------|------|------|------|
| TTS 語音不夠自然 | 中 | 高 | 多角色切換（5-6 種 voices）、加 BGM、節奏控制 |
| LLM 寫稿成本失控 | 低 | 中 | 用本地模型 fallback、cache 重複 topic |
| 訂閱者成長停滯 | 中 | 高 | 內容行銷 + 學校通路 + B2B 切入 |
| 內容版權爭議 | 低 | 中 | 強化 transformative work 設計、標註原始來源 |
| 競爭對手抄襲 | 中 | 中 | 累積訂閱者 + 品牌 + 客製化功能 |
| Alan 時間耗盡 | 高 | 高 | 自動化優先、減少人工審稿頻率 |

---

## 8. 啟動清單（MVP 90 天）

**Phase 1：技術打底（M1）**
- [ ] 寫稿 pipeline 整合 LLM API
- [ ] 網站雛形（Next.js + Supabase Auth）
- [ ] Stripe / TapPay 串接
- [ ] RSS feed 自動生成

**Phase 2：內容驗證（M2）**
- [ ] 連續 30 天日更
- [ ] A/B 測試主題（科技 vs 文化 vs 商業）
- [ ] 收集 10 位免費試聽者回饋

**Phase 3：變現（M3）**
- [ ] 開放 NT$149 月訂閱
- [ ] 啟用 Pro 層（NT$299）
- [ ] 接觸 3 間語言學校談 B2B

---

## 9. 待決定項目（給 Alan 勾選）

- [ ] 市場優先：B2C / B2B / 兩者並行？
- [ ] 個人 vs 公司：要以工作室名義營業登記嗎？
- [ ] 主題鎖定：上面 7 種主題，砍掉哪些？
- [ ] 個人 IP：要用 Alan 真人聲？還是純 TTS？
- [ ] 目標客群：台灣優先 / 東南亞 / 全球華人？
- [ ] 啟動資金預算：NT$0 開始（手動審稿）vs NT$20K（含廣告）？
- [ ] 時程：3 個月 MVP / 6 個月 / 12 個月？

---

## 10. 參考資料

- `references/taiwan-ecommerce-logistics.md` — 7-11 / 全家 取貨限制（數位訂閱不需要，但實體週邊商品會需要）
- `references/middleman-business-patterns.md` — 數位商品 middleman 模式（純數位零庫存）
- `../../Tailo/.hermes/plans/` — Tailo 商業計畫範本格式參考

---

## 附錄：競爭者連結（待 Alan 驗證）

> ⚠️ 自動化無法訪問台灣電商，以下為 Alan 需手動確認的競品連結
- VoiceTube: https://tw.voicetube.com/
- Engoo: https://engoo.com.tw/
- BBC Learning English: https://www.bbc.co.uk/learningenglish/
- 6 Minute English: https://www.bbc.co.uk/learningenglish/english/features/6-minute-english

---

**下一步建議**：Alan 看完這份衛星研究，告訴我
1. 哪些章節要進 V1.0 master plan（建議全部，但市場定位只選 B2C 或 B2B）
2. 哪些要砍掉 / 改寫
3. 待決定項目勾完後，我就整合成正式 master plan V1.0
