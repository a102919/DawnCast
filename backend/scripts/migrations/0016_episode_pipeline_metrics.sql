-- 記錄單集生成的分階段耗時 + 研究過程摘要，供事後追蹤瓶頸與研究品質。
--
-- 設計重點：
--   1. episodes 加 4 欄位：gen_metrics / research_metrics 走 jsonb（形狀定義在
--      backend/engine/pipeline/langgraph_pod/metrics.py），generation_started_at /
--      generation_finished_at 是 timestamptz 方便直接下 SQL 算 wall clock，不用拆 jsonb。
--   2. episode_pipeline_runs 是獨立 forensic 表：upsert_episode_node 之前的節點
--      （decompose / gather / cross_verify / verify_script_claims / quality_judge）
--      失敗時還沒有 episode row 可寫，這張表在 run_pod 開始就 INSERT，crash 也留得住。
--   3. 向後相容：既有 episodes 列補 '{}' 與 NULL，不影響既有查詢；新表不影響既有流程。

alter table public.episodes
  add column if not exists generation_started_at timestamptz,
  add column if not exists generation_finished_at timestamptz,
  add column if not exists gen_metrics jsonb not null default '{}'::jsonb,
  add column if not exists research_metrics jsonb not null default '{}'::jsonb;

comment on column public.episodes.generation_started_at is
    'worker 開始 run_pod 的時間（consume pgmq 訊息之後）';
comment on column public.episodes.generation_finished_at is
    '整個 pipeline 結束時間（成功或失敗都填）';
comment on column public.episodes.gen_metrics is
    '分階段耗時 + LLM call 明細；形狀見 langgraph_pod/metrics.py 的 GenMetrics';
comment on column public.episodes.research_metrics is
    '研究流程摘要（題數、來源數、verified claim、judge verdict）；形狀見 metrics.py 的 ResearchMetrics';

create table if not exists public.episode_pipeline_runs (
  run_id uuid primary key default gen_random_uuid(),
  idempotency_key text not null,
  episode_id uuid references public.episodes(id) on delete set null,
  attempt integer not null default 1,
  status text not null default 'running',
  enqueued_at timestamptz,
  started_at timestamptz,
  finished_at timestamptz,
  gen_metrics jsonb not null default '{}'::jsonb,
  research_metrics jsonb not null default '{}'::jsonb,
  error jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create unique index if not exists idx_pipeline_runs_idem_attempt
  on public.episode_pipeline_runs (idempotency_key, attempt);
create index if not exists idx_pipeline_runs_status
  on public.episode_pipeline_runs (status, created_at desc);

comment on table public.episode_pipeline_runs is
    'worker run forensic：run_pod 開始就 INSERT，pre-upsert 失敗/worker crash 也留得住紀錄；'
    'upsert_episode_node 完成時補上 episode_id，metrics 同時鏡像到 episodes。';
