# LangGraph Pod：實際應用後的優缺點

> 面試 cheat sheet — 用 DawnCast podcast pipeline 為例，講真實 trade-off。

## TL;DR

把 asyncio + pgmq 換成 LangGraph StateGraph **不是**明顯升級 — 兩者各有擅場。

**LangGraph 真贏的場景**（這次剛好遇到一個）：
- 寫稿 → 評分 → 不及格就重寫的**迴圈**，且要 cap 迭代次數

**asyncio + pgmq 真贏的場景**（DawnCast 的其他 90%）：
- 線性管線 + 強 retry / dead-letter 語意
- DB-native visibility timeout（pgmq 的 vt 是 Postgres 原生保證）
- ops 友善：Supabase / pg_cron / pgmq 都是現有基礎設施

---

## 對照表

| 維度 | asyncio + pgmq（原） | LangGraph pod（新） |
|------|---------------------|---------------------|
| 架構表達 | 多個 `async def` 串接，try/except 控制流 | 一個 StateGraph 描述「誰在誰之後、可選分支」 |
| 程式碼量 | 80 行（含 docstring 200 行） | 280 行 nodes + 90 行 graph = 370 行 |
| Retry 語意 | `_write_script` 內 try/except 手刻 generation vs rate-limit | `RetryPolicy(max_attempts=3, retry_on=GenerationError)` 宣告式 |
| 條件分支 | `if rate_limited: ...` 散落各函式 | `add_conditional_edges("write_script", rate_limit_decision, {...})` 集中 |
| 迴圈表達 | 需另寫 ad-hoc retry 層或第二個 pgmq queue | `add_edge("rewrite_iter_bump", "write_script")` 直接 back-edge |
| State 序列化 | 全部在函式 scope 內，函式間靠 DB 傳值 | 集中 TypedDict state，channel 可加 reducer |
| 觀測 | log + pgmq table | log + LangSmith（但本專案鎖 MiniMax，生態契合度低） |
| 測試 mock | monkeypatch module attribute | 注入 `config["configurable"]` runtime context |
| Checkpointing | 沒有，pgmq vt-retry 重跑整段 | 可加 PostgresSaver 做 partial-resume（V2） |
| Debug 入門 | 看 worker.py main loop 即可 | 需懂 StateGraph 編譯、conditional edge、configurable |
| 對既有基礎設施 | pgmq 已在 DB，零新東西 | 多兩個依賴（langgraph + langchain-core） |
| 生態契合 | 與 Supabase / pg_cron / pgmq 一致 | LangGraph 偏 OpenAI / Anthropic 生態（memory 鎖 MiniMax） |

---

## 詳細分析

### 1. 程式碼量 — LangGraph 變多了

**原版（80 行實作）：**
```python
async def _write_script(req, settings):
    engine = make_engine(settings)
    try:
        return await engine.write_script(req)
    except RateLimitError:
        if settings.failover_mode != "failover":
            raise
        fallback = make_engine(settings.model_copy(update={"generation_engine": "api_key"}))
        try:
            return await fallback.write_script(req)
        finally:
            await fallback.aclose()
    finally:
        await engine.aclose()
```

**LangGraph 版（拆 3 個檔案）：**
- `nodes.py` write_script_node 30 行
- `nodes.py` failover_write_script_node 30 行  
- `graph.py` add_conditional_edges + 路由函式 25 行
- 加上 MockRenderer、MockRepo、FakeChatModel 等測試 fixtures

**結論：** 同樣的邏輯，LangGraph 寫起來「拆得更細」但 **code 沒比較少**。Linus 會說：把 if/else 換成 declarative graph 並沒有減少 special cases，只是把它們搬到了另一個地方。

### 2. RetryPolicy 真的比較乾淨

**原版：** `_write_script` 一個函式裡同時處理：
- GenerationError（語意層重生）
- RateLimitError（failover 觸發）
- 5xx（傳輸重試）
- aclose 資源釋放

四件事混在一起，讀者要 trace try/except 才知道哪層是 retry 哪層是 failover。

**LangGraph 版：**
```python
builder.add_node("write_script", write_script_node,
    retry_policy=RetryPolicy(max_attempts=3, retry_on=GenerationError))
builder.add_node("failover_write_script", failover_write_script_node,
    retry_policy=RetryPolicy(max_attempts=3, retry_on=GenerationError))
# RateLimitError → conditional edge → failover node（不在 retry 範圍）
```

每個 node 各自宣告「我 retry 什麼」，讀 graph.py 一次就懂。

**結論：** **LangGraph 在這點真的比較好**。RetryPolicy 跟 conditional edge 兩件事分開，比 try/except 巢狀清楚。

### 3. judge → rewrite 迴圈 — LangGraph 完勝

**這是唯一一個 LangGraph 明顯贏的場景。**

**原版要怎麼做？** 兩個選項都很醜：
- 在 `_write_script` 加 for-loop retry on judge score：把「重試寫稿」跟「語意重生」混在一起。
- 加第二個 pgmq queue `generate.rewrite`：新 infra、新 failure modes、新 ops surface、需另寫 worker。

**LangGraph 版：**
```python
builder.add_conditional_edges("quality_judge", judge_decision,
    {"upsert": "upsert_episode", "rewrite": "rewrite_iter_bump"})
builder.add_edge("rewrite_iter_bump", "write_script")
```

兩個 edge 講完。`judge_decision` 函式就是 conditional edge 的 path function，5 行：
```python
if _judge_passed(scores, threshold) or iterations >= max_iter:
    return "upsert"
return "rewrite"
```

**結論：** **這是 LangGraph 真正擅長的 pattern** — declarative cycle with cap。如果沒有 cycle，LangGraph 沒有明顯優勢。

### 4. pgmq vt-retry — asyncio 完勝

**pgmq 強在哪：**
- `read_ct` 自動遞增，`archive` 在 N 次後自動觸發
- vt 到期自動重投（worker 不在時也 work）
- Supabase Dashboard 直接看 message / log
- 跟現有 pg_cron / observability stack 一致

**LangGraph 怎麼做：** 要自己寫 RetryPolicy（已有），但**要 partial-resume 需要 PostgresSaver**（我們沒接）。如果只想「整段重試」，pgmq vt 反而更簡單。

**結論：** **不要為了 LangGraph 把 pgmq 換掉。** worker.py 的 main loop 保持現狀是對的。

### 5. Mock 與測試 — 兩者打平

**asyncio 版：** `monkeypatch.setattr(generate_job, "make_engine", fake)` 直接替換依賴。
**LangGraph 版：** 注入 `config["configurable"]["chat"]` / `["repo"]` 等到 runtime context。

兩者都要寫假件。LangGraph 的好處：假件不用 monkeypatch，比較「乾淨」；壞處：寫測試的人要懂 LangGraph 的 config 機制。

### 6. State 共享 — 各有千秋

**asyncio：** 函式間靠 `return value` 與 DB row 傳值，scope 天然隔離。
**LangGraph：** 全域 TypedDict state，channel 可加 `Annotated[list, operator.add]` reducer 做累積。

**踩坑：** 一開始我用 `_call_count` 一個 counter 同時給 `responses` 跟 `judge_responses` 兩個池子，結果第二次 judge call 拿到第三次 writer 的回應（pool index 錯位）。修法是拆 `_writer_count` / `_judge_count` 兩組。**這是 fake pool 設計的 bug，不是 LangGraph 的問題**，但它示範了：LangGraph 的「跨 node 共享狀態」比 asyncio 「每個函式自己 scope」更容易踩共享狀態的雷。

### 7. 觀測 — asyncio 簡單、LangGraph 多選擇

- **asyncio：** `logger.info(...)` + 看 pgmq table + 看 Supabase log。沒新工具。
- **LangGraph：** 可掛 LangSmith（hosted）、可自己寫 callback、可加 PostgresSaver 重播。

對 mini side-project：LangSmith 偏 Anthropic / OpenAI 生態，跟 DawnCast 鎖的 MiniMax 不契合。**建議不接 LangSmith**，維持跟原版一樣的 logger-only 觀測。

---

## 真正的收穫

把 podcast pipeline 用 LangGraph 寫一遍，最大的收穫不是「更短」或「更快」。

是 **judge → rewrite 迴圈可以用 declarative back-edge 寫出來**。

這個 pattern 在面試時拿出來講，價值在於：
1. **展示 trade-off 思考**：不是所有東西都該用 LangGraph
2. **展示 cycle 處理**：很多面試官會問「你的 agent loop 怎麼做」
3. **展示 declarative 思維**：conditional edge 比 imperative if/else 更易讀

**面試話術建議：**
> 「我之前的工作流是 asyncio + pgmq。後來為了把 judge → rewrite 迴圈寫得乾淨，把核心路徑 port 到 LangGraph。pgmq 那層保留作 retry 跟 dead-letter。
> 
> 結論：LangGraph 的 cycle edge 真的比較好寫，但 80% 的場景 pgmq 已經夠用。如果新工作流有 cycle、HITL、tool calling 這三種需求，我會直接選 LangGraph。」

---

## 何時該 / 不該選 LangGraph

### 選 LangGraph 的時機 ✅
- 流程有 **cycle**（grade → regenerate, simulate → adjust）
- 流程有 **HITL**（人在某個 node 插隊 review）
- 多步 LLM 工具呼叫（agent 框架的核心 use case）
- 需要 **partial resume**（某個 node 失敗後，從該 node 重啟而非整段重跑）
- 想要 **LangSmith 觀測**（團隊偏 OpenAI / Anthropic 生態）

### 別選 LangGraph 的時機 ❌
- 線性管線 + DB 既有 queue（pgmq / SQS / Redis Streams）→ 加抽象層沒換來價值
- 強 retry / dead-letter 語意是 DB-native 強約束 → 不要換成 Checkpointer
- 團隊不熟 LangGraph → 學習曲線比 plain asyncio 陡
- ops 環境沒有 vector clock / time-travel 需求 → `graph.ainvoke(state)` 比 `await pipeline(state)` 沒比較好

---

## 對 DawnCast 的具體建議

| 元件 | 維持原樣 | 改用 LangGraph |
|------|---------|---------------|
| `worker.py`（pgmq 主迴圈） | ✅ | ❌ — pgmq vt 是 DB 保證，換掉虧 |
| `reuse.py`（anti-join 命中/未命中） | ✅ | ❌ — 太線性，加 graph 沒換來什麼 |
| `evergreen.py`（未交付者兜底） | ✅ | ❌ — 單一函式 |
| `generate_job.py`（核心管線） | — | ✅（已 port）— judge→rewrite cycle 是真正贏點 |
| `post_process.py`（dict 翻譯 enqueue） | ✅ | ❌ — 一行 send 沒需要包 graph |

**結論：** 對 DawnCast，**這個 port 是 learning exercise，不是 upgrade**。如果重來，我會：
1. 保留 pgmq + worker.py 不動
2. 只把 `run_generate_job` 換成 LangGraph pod
3. 為了 cycle，把 judge node + rewrite 流程獨立成 subgraph
4. 不接 LangSmith（生態不契合）
