-- 每日批次從「固定 2 筆」放寬成「可變筆數（0~10）」，讓頻道機制決定當天實際
-- 要生幾集（見 shared/db/channels.py 的 pick_daily_topics：候選不足就少產，
-- 甚至 0 集，不硬湊）。同時新增 channel_plan 的每日 cron 觸發。
--
-- 三件事：
--   1. daily_podcast_runs.enqueued_count 的 check constraint 從 0~2 放寬到 0~10
--      （0021 的 channel_daily_max_slots 預設 4，10 是留給未來調高的天花板）。
--   2. enqueue_daily_podcast_batch(date, jsonb) 拿掉「必須剛好 N 筆」的硬檢查，
--      改成允許 0~10 筆；同時把「回傳值的語意」拆乾淨：
--        -1 = 這個 deliver_date 已經被別人 claim 過（marker INSERT 衝突，
--             本次呼叫完全沒動到任何東西，不是「送了 0 筆」）。
--        >=0 = 這次呼叫確實 claim 到 marker 並跑完，實際送出的筆數——
--              可以是 0（今天評估過候選但沒有夠格的主題，「沒內容就不產」
--              是刻意設計，不是異常）。
--      兩者都會寫 marker、都有 completed_at，差別只在「這次呼叫有沒有真的
--      跑迴圈」，用回傳值本身就能區分，呼叫端不需要再查
--      daily_podcast_runs 表。
--   3. 新增 pg_cron job dawncast-channel-plan：01:00 台北時區送 control 訊息
--      觸發選題（跑在 02:00 的 dawncast-daily-podcast 之前，確保選題庫在
--      當天生成排程前已經補過）。
--
-- 前置條件：0014_daily_podcast_cron.sql 建立 daily_podcast_runs / 原始 function /
-- dawncast-daily-podcast cron；0020_daily_podcast_batch_size.sql 曾把上限從 5 改 2
-- （本檔取代 0020 對 function 與 constraint 的版本，cron job 本身不動）。

create extension if not exists pgmq;
create extension if not exists pg_cron;

-- ── 1. constraint 放寬 0~2 → 0~10 ────────────────────────────────────
alter table public.daily_podcast_runs
    drop constraint if exists daily_podcast_runs_enqueued_count_check;

alter table public.daily_podcast_runs
    add constraint daily_podcast_runs_enqueued_count_check
    check (enqueued_count between 0 and 10);

-- ── 2. enqueue_daily_podcast_batch：可變筆數 + -1/實際筆數語意 ──────────
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

    -- jsonb_array_length 對合法陣列恆 >= 0，下界天然滿足；只需擋上界。
    if jsonb_array_length(p_messages) > 10 then
        raise exception
            'daily_podcast batch must contain at most 10 messages, got %',
            jsonb_array_length(p_messages);
    end if;

    -- 同 deliver_date 只有第一個 transaction 能成功 INSERT；
    -- concurrent caller（或同日第二次 control 訊息）看到 conflict 後直接
    -- return -1——這不是「送了 0 筆」，是「這天根本沒跑到迴圈」，兩者
    -- 語意不同，回傳值必須能分開，呼叫端才知道要不要當成異常記錄。
    insert into public.daily_podcast_runs (deliver_date)
    values (p_deliver_date)
    on conflict (deliver_date) do nothing;

    if not found then
        return -1;
    end if;

    -- marker + N 筆 pgmq.send 全在同一 transaction。p_messages 可以是空陣列
    -- （candidates 不足時），迴圈直接 0 次，仍會走到下面的 UPDATE 寫 marker
    -- 完成，語意＝「今天評估過，沒東西可產」。
    -- 任一 send 失敗 → function 內 raise → 整批 rollback（marker 也撤回），
    -- 下一次 control 可完整重試。
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
    'daily_podcast 原子 enqueue：marker INSERT + 0~10 筆 send 同 transaction；'
    '已被 claim 回 -1，正常完成回實際送出筆數（可為 0＝評估過但沒東西可產）；'
    '任一 send 失敗整批 rollback。';

-- ── 3. pg_cron job：01:00 台北時區觸發頻道選題 ──────────────────────
-- 01:00 早於 02:00 的 dawncast-daily-podcast，確保選題庫在當天生成排程
-- （pick_daily_topics）跑之前已經補過候選。date 帶台北日曆日，與既有
-- collect-open / orchestrate / daily-podcast 同公式。
do $migration$
declare
    v_old_job_id bigint;
begin
    -- rerun-safe：先清掉同名 job 再 schedule，避免 migration 重跑產生多個 job。
    for v_old_job_id in
        select jobid from cron.job where jobname = 'dawncast-channel-plan'
    loop
        perform cron.unschedule(v_old_job_id);
    end loop;

    perform cron.schedule(
        'dawncast-channel-plan',
        '0 1 * * *',
        $cron$
            select pgmq.send(
                'control',
                jsonb_build_object(
                    'task', 'channel_plan',
                    'date', (now() at time zone 'Asia/Taipei')::date::text
                )
            )
        $cron$
    );
end
$migration$;
