-- 每日公開 podcast 批次數量：5 → 2。
-- 只調整 0014 建立的 enqueue_daily_podcast_batch() 筆數檢查與
-- daily_podcast_runs.enqueued_count check constraint，其餘 marker/cron 邏輯不動。

alter table public.daily_podcast_runs
    drop constraint if exists daily_podcast_runs_enqueued_count_check;

alter table public.daily_podcast_runs
    add constraint daily_podcast_runs_enqueued_count_check
    check (enqueued_count between 0 and 2);

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

    if jsonb_array_length(p_messages) <> 2 then
        raise exception
            'daily_podcast batch must contain exactly 2 messages, got %',
            jsonb_array_length(p_messages);
    end if;

    insert into public.daily_podcast_runs (deliver_date)
    values (p_deliver_date)
    on conflict (deliver_date) do nothing;

    if not found then
        return 0;
    end if;

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
    'daily_podcast 原子 enqueue：marker INSERT + 2 send 同 transaction；'
    '同日第二次回 0，任一 send 失敗整批 rollback。';
