-- 生成佇列（pgmq）+ 夜間排程（pg_cron）。PRD §3.1 / §3.2 / §5。
-- pg_cron 只負責 tick：發 pgmq 控制訊息，外部 I/O（LLM/嵌入）全在 worker，
-- DB 內不打外部 HTTP（無 timeout/retry 控制）。
--
-- 時區：排程牆鐘時間以 DB 時區為準，Supabase 預設 UTC。上線前須將 DB 時區設為
-- 'Asia/Taipei'（或把下方 cron 時間換算成對應 UTC），否則 22:00/23:00/03:30 會跑錯時刻。
-- 日期錨點：訊息一律帶 'date'＝台北日曆日（now() at time zone 'Asia/Taipei'），
-- 讓 worker 不必依賴容器本機 date.today()（通常 UTC），跨午夜也不會與 daily_orders.order_date 對不上。
-- 用 jsonb_build_object 直接產 jsonb（pgmq.send 第二參數要 jsonb，免去字串字面量轉型歧義）。

create extension if not exists pgmq;
create extension if not exists pg_cron;

-- 兩條佇列：control（orchestrate/evergreen）與 generate（單集）
select pgmq.create('control');
select pgmq.create('generate');

-- 22:00 開收集窗（標記當日預約進入正規化階段）
select cron.schedule(
  'dawncast-collect-open', '0 22 * * *',
  $$ select pgmq.send('control', jsonb_build_object(
       'task', 'collect_open',
       'date', (now() at time zone 'Asia/Taipei')::date::text)) $$
);

-- 23:00 預約截止 → 觸發 orchestrate（worker 跑 A~E：正規化/嵌入/聚類/重用/enqueue）
select cron.schedule(
  'dawncast-collect-close', '0 23 * * *',
  $$ select pgmq.send('control', jsonb_build_object(
       'task', 'orchestrate',
       'date', (now() at time zone 'Asia/Taipei')::date::text)) $$
);

-- 03:30 evergreen 兜底：對未交付者全補常青集（黎明 SLA 與生成成功率解耦）
-- 注意：03:30 已過午夜，台北日曆日為「隔天」，與 orchestrate（前一天 23:00）錨定的
-- order_date 不同。evergreen 補的是「orchestrate 當天受理、應於本次黎明交付」那批，
-- 故日期回退一天對齊 orchestrate 的錨點。
select cron.schedule(
  'dawncast-evergreen', '30 3 * * *',
  $$ select pgmq.send('control', jsonb_build_object(
       'task', 'evergreen',
       'date', ((now() at time zone 'Asia/Taipei')::date - 1)::text)) $$
);
