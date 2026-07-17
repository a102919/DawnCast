# Migrations

依序套用（Supabase SQL Editor 或 psql）：

1. `0001_init.sql` — 核心表 + handle_new_user trigger
2. `0002_rls.sql` — RLS 縱深防禦 policy
3. `0003_queue_cron.sql` — pgmq 佇列 + pg_cron 夜間排程
4. `0004_vocab_source_sentence_zh.sql` — user_vocab 補來源句子中文對照
5. `0005_episode_grounding_format.sql` — episodes 加 length_tier / format / grounded 欄位
6. `0006_episode_token_usage.sql` — 新增 episodes.input_tokens / output_tokens 欄位（GET /admin/token-usage 依賴）
7. `0007_daily_orders_entry_mode.sql` — daily_orders 加 entry_mode / length_tier，topic_requests 加 length_tier
8. `0008_daily_orders_status_check.sql` — daily_orders 加 daily_orders_status_check CHECK constraint
9. `0009_user_activity.sql` — 新增 user_activity 表（streak/聆聽分鐘/查詞次數/播放進度上雲）

> `0003` 需要 Supabase 啟用 `pgmq` 與 `pg_cron` extension（Dashboard → Database → Extensions）。
> pgvector 起步不建 ivfflat/hnsw 索引（資料量小，精確掃描更穩，PRD §3.1）。
>
> **時區**：`0003` 的 cron 牆鐘時間（22:00 / 23:00 / 03:30）以 DB 時區為準。
> Supabase 預設 UTC，上線前須設為 Asia/Taipei：
> `alter database postgres set timezone to 'Asia/Taipei';`
> （訊息已內含台北日曆日當錨點，worker 不受容器時區影響；但排程「時刻」仍依 DB 時區。）
