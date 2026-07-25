-- 每日公開 podcast 批次：02:00 自動送 control 訊息 → worker enqueue 5 部公開集
-- 進 generate 佇列（user_ids=[]、source='daily_batch' → is_free=true → reuse L1 池）。
--
-- 設計重點：
--   1. daily_podcast_runs marker table：同日 exactly-once claim。
--   2. SECURITY DEFINER enqueue_daily_podcast_batch(date, jsonb) PL/pgSQL function：
--      marker INSERT + 5 筆 pgmq.send 全在同一 transaction，
--      任一 send 失敗整批 rollback（marker 也撤回，下一次 control 可完整重試）。
--   3. do $migration$ block：rerun-safe unschedule 同名 cron job，避免 migration 重跑
--      建立多個重複 job。
--
-- 前置條件：
--   0003_queue_cron.sql 已建立 pgmq、pg_cron 與 control / generate queue。
--
-- 時區：
--   cron wall-clock 依 DB timezone；既有的 collect-open / orchestrate / evergreen
--   假設 DB timezone 為 Asia/Taipei 或 cron 時間已換算成 UTC。本 migration 不動這塊。

create extension if not exists pgmq;
create extension if not exists pg_cron;

-- ── Marker table：同日 exactly-once claim ────────────────────────────
create table if not exists public.daily_podcast_runs (
    deliver_date    date primary key,
    enqueued_count  smallint not null default 0
        check (enqueued_count between 0 and 5),
    created_at      timestamptz not null default now(),
    completed_at    timestamptz
);

comment on table public.daily_podcast_runs is
    'daily_podcast 批次 marker：同日第二次 enqueue 直接 noop，避免重複送 LLM。';

-- ── Atomic enqueue function ──────────────────────────────────────────
-- SECURITY DEFINER：runtime DB role 不需直接擁有 marker table 寫入權限。
-- set search_path = public, pgmq：避免 schema 搜尋被 hijack（security best practice）。
create or replace function public.enqueue_daily_podcast_batch(
    p_deliver_date date,
    p_messages     jsonb
)
returns integer
language plpgsql
security definer
set search_path = public, pgmq
as $$
declare
    v_message     jsonb;
    v_sent_count  integer := 0;
begin
    if p_deliver_date is null then
        raise exception 'daily_podcast deliver_date cannot be null';
    end if;

    if p_messages is null or jsonb_typeof(p_messages) <> 'array' then
        raise exception 'daily_podcast messages must be a JSON array';
    end if;

    if jsonb_array_length(p_messages) <> 5 then
        raise exception
            'daily_podcast batch must contain exactly 5 messages, got %',
            jsonb_array_length(p_messages);
    end if;

    -- 同 deliver_date 只有第一個 transaction 能成功 INSERT；
    -- concurrent caller 看到 conflict 後直接 return 0（不會留下半完成 marker）。
    insert into public.daily_podcast_runs (deliver_date)
    values (p_deliver_date)
    on conflict (deliver_date) do nothing;

    if not found then
        return 0;
    end if;

    -- marker + 5 筆 pgmq.send 全在同一 transaction。
    -- 任一 send 失敗 → function 內 raise → 整批 rollback（marker 也撤回），
    -- 下一次 control 可完整重試 5 筆。
    for v_message in
        select value from jsonb_array_elements(p_messages) as item(value)
    loop
        perform pgmq.send('generate', v_message);
        v_sent_count := v_sent_count + 1;
    end loop;

    update public.daily_podcast_runs
    set enqueued_count = v_sent_count,
        completed_at   = now()
    where deliver_date = p_deliver_date;

    return v_sent_count;
end;
$$;

comment on function public.enqueue_daily_podcast_batch(date, jsonb) is
    'daily_podcast 原子 enqueue：marker INSERT + 5 send 同 transaction；'
    '同日第二次回 0，任一 send 失敗整批 rollback。';

-- ── pg_cron job：02:00 台北時區送 control 訊息 ──────────────────────
-- 02:00 早於 03:30 evergreen、早於用戶 07:00 早餐交付，確保 L1 公開集
-- 在 orchestrate 收單前已備妥。date 用當天台北日（不減 1），跟 collect-open
-- 同公式；02:00 仍在當天日曆內。
do $migration$
declare
    v_old_job_id bigint;
begin
    -- rerun-safe：先清掉同名 job 再 schedule，避免 migration 重複跑產生多個 job。
    for v_old_job_id in
        select jobid from cron.job where jobname = 'dawncast-daily-podcast'
    loop
        perform cron.unschedule(v_old_job_id);
    end loop;

    perform cron.schedule(
        'dawncast-daily-podcast',
        '0 2 * * *',
        $cron$
            select pgmq.send(
                'control',
                jsonb_build_object(
                    'task', 'daily_podcast',
                    'date', (now() at time zone 'Asia/Taipei')::date::text
                )
            )
        $cron$
    );
end
$migration$;
