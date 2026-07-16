# DawnCast dict_cache 灌庫策略

> 狀態：規劃中，2026-07-12 拍板
> 取代：舊 PRD §7.5「ECDICT 客戶端離線首查」假設（已不合實際架構：前端走 `/dict/lookup` HTTP，DB 後端表 0 筆）

---

## 1. 問題陳述

`backend/app/routers/dict.py:lookup_dict` 從 `public.dict_cache` 表查字（`word / ipa / pos / translation / exchange / audio_url`）。目前 **0 筆資料**，所有點字 → `/dict/lookup` miss → 回 `null` → 前端 `WordCardPanel` 顯示「查詢失敗」。

**目標**：灌進 760k 字典 + 100k 音檔，全部走 **零成本 / 商用授權乾淨** 的開源資料。

---

## 2. 架構（最終決定）

```
[新集上架]
   ↓ generate_job 完成
[diff dict_cache] → 缺的字
   ↓
[enqueue dict.translate queue]（MiniMax worker，計翻譯+IPA+例句）
   ↓
[upsert dict_cache]（含 translation / ipa / pos）
   ↓
[nightly cron: backfill_audio]（Piper TTS 自架補 audio_url）
   ↓
[用戶點字] → GET /dict/lookup → 命中 → 顯示詞卡 + 音檔
                              ↓ miss（罕見）
                              LLM fallback（同 MiniMax）
```

**關鍵變更 vs 舊 PRD**：
- ~~ECDICT 塞前端 bundle~~ → 改成 **ECDICT 灌後端 dict_cache**
- 新增 kaikki.org JSONL 補 audio / IPA（內部用，授權乾淨）
- LLM fallback 介面已實作（`dict.py:43` TODO），worker 共用 podcast 同 engine

---

## 3. 資料來源（已驗證）

| 來源 | 角色 | 授權 | 規模 | URL |
|---|---|---|---|---|
| **ECDICT** | 主字典（word + zh + ipa + exchange） | **MIT**（商用 OK） | 760k 詞 | https://github.com/skywind3000/ECDICT |
| **kaikki.org JSONL** | 補 audio / IPA / 例句 | CC BY-SA 4.0（**內部用安全**） | 1.38M senses | https://kaikki.org/dictionary/English/kaikki.org-dictionary-English.jsonl |
| **CMUdict** | IPA 補完（給 Piper 合成） | 公領域 / BSD | 134k | http://www.speech.cs.cmu.edu/cgi-bin/cmudict |
| **Piper TTS** | 自合單字音檔 | **MIT**（商用 OK，espeak-ng GPL 採 dynamic linking） | ONNX CPU | https://github.com/rhasspy/piper |

**避開**：
- ❌ edge-tts / gTTS — 商用 ToS 紅線（Microsoft / Google endpoint 禁自動化商業流量）
- ❌ Coqui / ChatTTS — 已倒 / NC 授權
- ❌ Wiktionary 直接 dump — share-alike 對閉源商業產品是禁區（kaikki 是內部派生，安全）
- ❌ CC-CEDICT — 方向反（中→英）+ share-alike
- ❌ Forvo API — 商用 $28.95/月，無 bulk download

---

## 4. 灌庫流程（ponytail 最小版）

### 4.1 一次性 backfill（兩個 script，獨立於 generate）

**Layer 0**：`scripts/seed_dict_cache.py`（~80 行）
```bash
# 1. 下載（~3.3GB 一次性）
wget -O /tmp/ecdict.csv https://github.com/skywind3000/ECDICT/releases/latest/download/ecdict.csv
wget -O /tmp/kaikki-en.jsonl https://kaikki.org/dictionary-English/kaikki.org-dictionary-English.jsonl

# 2. ECDICT 簡→台繁（OpenCC s2twp）
#    翻譯欄位跑 s2tw.convert：滑鼠→滑鼠、網路→網路

# 3. kaikki 純化抽 audio+ipa+zh（jq 或 python stream）
#    取 sounds[0].audio / ipa / translations[?code=='zh'].word

# 4. Postgres COPY + UPSERT 到 dict_cache
./backend/.venv/bin/python -m scripts.seed_dict_cache
```
**預期結果**：dict_cache 760k row、有 `translation / ipa / pos / exchange` 4 欄，`audio_url` 待 Layer 1

### 4.2 音檔批次合成（nightly cron）

**Layer 1**：`scripts/backfill_audio.py`（~50 行）
```bash
# 撈 dict_cache where audio_url is null limit 500
# Piper subprocess 批次合成 mp3
# 上傳 R2（key=audio/dict/{word}.mp3）或本地 fallback
# 寫回 dict_cache.audio_url
./backend/.venv/bin/python -m scripts.backfill_audio --limit 5000
```
**預期結果**：100k 單字在 16 核 CPU 約 30 分鐘跑完 → 5GB mp3 → 後續零成本

### 4.3 新集自動補缺字（generate_job 後掛鉤）

**Layer 2**：`engine/pipeline/post_process.py`（~30 行）
```python
async def backfill_dict(target_vocab: list[TargetVocab]) -> int:
    words = [v.word.casefold() for v in target_vocab]
    async with connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select word from public.dict_cache where word = any(%s)", (words,)
        )
        existing = {r[0] for r in await cur.fetchall()}
    missing = [w for w in words if w not in existing]
    if missing:
        await repo.enqueue_translation_jobs(missing)
    return len(missing)
```
worker 共用 `make_engine(settings)`（**MiniMax**，與 podcast 同一條）

**規則**：best-effort，**失敗不擋 generate**（try/except logger warning）

---

## 5. 授權合規檢核

| 元件 | 授權 | 商用 | 對應處理 |
|---|---|---|---|
| ECDICT | MIT | ✅ | 直接用，保留 LICENSE |
| kaikki JSONL | CC BY-SA 4.0 | ✅ 內部用 | **dict_cache 不對外 export**（純 app 內部查詢） |
| Piper TTS | MIT | ✅ | espeak-ng 採 dynamic linking |
| Piper 合成的 mp3 | 自己產的，不繼承上游 share-alike | ✅ | 完整所有權 |
| OpenCC | Apache 2.0 | ✅ | 直接用 |
| Tatoeba Sentences | CC0 | ✅ | 例句日後用（dict_cache 無例句欄位，暫不灌） |

**風險邊界**：
- ✅ **dict_cache 內容純內部用**（API endpoint 提供給 app 查詢，不對外 dump）→ kaikki share-alike 不觸發
- ⚠️ **若未來要做「匯出我的單字本」功能**：匯出的是 `user_vocab`（用戶自填），非 `dict_cache`，不受影響
- ⚠️ **若未來要做「字典瀏覽器頁面」對外開放**：必須重驗 kaikki 該部分授權，或改用純 ECDICT 顯示

---

## 6. 成本

| 階段 | 一次性 | 持續（30 集/月） |
|---|---|---|
| ECDICT + kaikki 下載 | $0 | — |
| Piper backfill 100k | $0（CPU 30 分鐘） | — |
| MiniMax 補缺字 worker | — | 3 字/集 × 30 = 90 字 × 200 tok ≈ **18K tok/月**（**MiniMax 訂閱額度內**，$0） |
| R2 儲存 100k mp3 | — | 5GB × $0.015 = **$0.075/月** |
| **總計** | $0 | **< $1/月** |

用量翻 10 倍（300 集/月）也才 ~$10/月，**MiniMax 訂閱制成本完全可控**。

---

## 7. 已知坑（實作時必看）

1. **ECDICT translation 是簡體** → 必須 OpenCC `s2twp`（滑鼠→滑鼠、網路→網路、台灣詞彙校正）
2. **kaikki 一行是一個 sense**（不是 headword），`LOWER(word)` 會撞 → `ON CONFLICT DO UPDATE` 處理
3. **kaikki 部分 audio 非 CC0**（混 GFDL / public domain）→ **不採用 kaikki audio**，統一走 Piper 自合（授權最乾淨）
4. **kaikki 檔案 3GB** → 用 `ijson` stream parse，不要一次讀進記憶體
5. **Piper 預設只接受小寫** → 單字先 `.casefold()` 再丟
6. **Piper 合成的結尾爆破音**（如 "quantum" 的 m）偶爾會被吞 → 選 `en_US-amy-medium` voice 最穩
7. **espeak-ng GPLv3** → 不要 static link 進閉源 binary；docker 分發視同 conveyance，注意 license 文件
8. **CEFR 等級標記**：ECDICT 沒 CEFR，但有 `oxford 0/3000/5000`、`collins 0-5`、`bnc` 詞頻 → 可用 `oxford >= 1 OR collins >= 2` 推估 B1，先全部灌，CEFR 標籤日後用 `user_vocab` table 另外標

---

## 8. 實作順序

| Step | 任務 | 預估工時 | 風險 |
|---|---|---|---|
| 1 | 寫 `scripts/seed_dict_cache.py` | 1 小時 | 低（純 DB 灌資料） |
| 2 | 寫 `scripts/backfill_audio.py` | 30 分鐘 | 低（Piper CLI 包 subprocess） |
| 3 | 寫 `engine/pipeline/post_process.py` | 30 分鐘 | 中（要接 queue worker） |
| 4 | 在 `generate_job.py` 掛 `post_process.backfill_dict()` | 15 分鐘 | 低（try/except 包好） |
| 5 | 跑 Layer 0 + Layer 1 | 30 分鐘（背景） | 低 |
| 6 | 驗證 `/dict/lookup` 真實命中率 | 15 分鐘 | — |

**全部 commit 在一個 PR**（feature/dict-cache-backfill）。

---

## 9. 不在這次範圍（P2 之後）

- 例句獨立表（`example_sentences(word, en, zh)`）：Tatoeba CC0 灌進去
- Wiktionary 中文翻譯回灌（內部用授權安全，但需要 parser）
- CEFR 等級標籤（用 `oxford` / `collins` 推 B1 名單）
- 前端高亮已存單字（PRD §7.5 已規劃）
- SRS 演算法（PRD §7.5 已規劃）

---

## 10. 與 PRD §7.5 對齊聲明

舊 PRD §7.5 規劃「ECDICT 塞前端 bundle」。本文件**取代**該子章節：

- **保留**：ECDICT（MIT）、CMUdict、Piper TTS、單字本 server-side 綁 user_id 這四個核心決策
- **修正**：ECDICT 從前端 bundle → 後端 `dict_cache` 表（事實上前端 mock 模式仍可打包 dict.json 做離線 fallback，但 real 模式後端是主要路徑）
- **新增**：kaikki.org JSONL 補 IPA（內部用授權安全）、MiniMax worker 補缺字（與 podcast 同 engine）
- **移除**：~~「5K 詞前端覆蓋率 ~85%」~~（改全量後端覆蓋率 ~99%）

---

## 附錄：dict_cache schema（已存在）

```sql
-- backend/db/migrations/0001_init.sql:143
create table public.dict_cache (
  word        text primary key,        -- lowercase
  ipa         text,
  pos         jsonb not null default '[]',
  translation text not null,
  exchange    text,
  audio_url   text,
  created_at  timestamptz not null default now()
);
-- backend/db/migrations/0002_rls.sql:34
-- 全人可讀；service_role 可寫
```

**欄位對映**（最終）：

| dict_cache | ECDICT | kaikki | Piper |
|---|---|---|---|
| word | word (casefold) | word (casefold) | — |
| ipa | phonetic | sounds[0].ipa | — |
| pos | pos（拆 jsonb） | pos | — |
| translation | translation（OpenCC s2twp） | translations[zh].word | — |
| exchange | exchange | forms | — |
| audio_url | — | — | `/audio/dict/{word}.mp3` |
