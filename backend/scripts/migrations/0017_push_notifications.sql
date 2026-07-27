-- Web Push 通知：訂閱表 + 每日通知去重欄位 + 每 5 分鐘的 push_daily tick。
--
-- 去重設計：不建 marker 表。deliveries.notified_at 就是「這筆交付通知過了沒」，
-- claim_daily_notifications 用一條
--   update ... where notified_at is null returning user_id
-- 同時完成「篩出該通知的人」與 atomic claim；cron 掃幾次都只會推一次。
-- 所以時間判定可以放寬成「delivery_time <= now」而不必分鐘精確比對，
-- worker 重啟或 cron 漂移不會整天漏發。
--
-- 開關設計：關閉通知＝刪掉該裝置的 push_subscriptions 列，不加 push_enabled 欄位。
-- Notification.permission 一旦 granted 不會再彈窗，重新開啟不會騷擾使用者；
-- 且多裝置語意自然（這台關不影響另一台）。

create extension if not exists pgmq;
create extension if not exists pg_cron;

-- ── 訂閱表 ───────────────────────────────────────────────────────
-- endpoint 當 PK：push service（FCM / Mozilla autopush）保證全域唯一，
-- 一個 user 多裝置就是多列。
create table if not exists public.push_subscriptions (
  user_id    uuid not null references public.users(id) on delete cascade,
  endpoint   text primary key,
  p256dh     text not null,
  auth       text not null,
  created_at timestamptz not null default now()
);

create index if not exists idx_push_subs_user on public.push_subscriptions (user_id);

comment on table public.push_subscriptions is
  'Web Push 訂閱（VAPID）。一個 user 可有多列（多裝置）；'
  '關閉通知＝刪列，故「有列」即代表該裝置要收通知。';

-- ── 每日通知去重 ─────────────────────────────────────────────────
alter table public.deliveries add column if not exists notified_at timestamptz;

comment on column public.deliveries.notified_at is
  '出餐通知已推送的時間。null＝還沒推；UPDATE ... WHERE notified_at is null 做 atomic claim。';

-- ── pg_cron：每 5 分鐘 tick 一次 push_daily ───────────────────────
-- 5 分鐘而非每分鐘：有 notified_at 去重後掃描頻率只影響延遲上限，
-- 5 分鐘的誤差在「早餐通知」情境使用者感覺不出來，DB 負擔也更低。
-- date 帶台北日曆日，與 collect-open / orchestrate 同公式。
do $migration$
declare
  v_old_job_id bigint;
begin
  -- rerun-safe：先清同名 job，避免 migration 重跑產生多個 job。
  for v_old_job_id in
    select jobid from cron.job where jobname = 'dawncast-push-daily'
  loop
    perform cron.unschedule(v_old_job_id);
  end loop;

  perform cron.schedule(
    'dawncast-push-daily',
    '*/5 * * * *',
    $cron$
      select pgmq.send(
        'control',
        jsonb_build_object(
          'task', 'push_daily',
          'date', (now() at time zone 'Asia/Taipei')::date::text
        )
      )
    $cron$
  );
end
$migration$;
