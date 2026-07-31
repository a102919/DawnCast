-- 每日點餐：從「一天一筆」改成「隨時可點、佇列制」。
-- 佇列制＝同一時間只允許一筆「進行中」（pending/queued）訂單，完成（played）
-- 才能點下一筆；歷史上允許同一天多筆訂單。
--
-- 不動 channels / channel_topics / daily_podcast / channel_plan 任何一行——
-- 頻道自動生成是完全獨立的另一套機制，已逐行核對零耦合。

-- ── 1. daily_orders：id 當 PK，order_date 降級為一般欄位 ─────────────
alter table public.daily_orders add column if not exists id uuid;
update public.daily_orders set id = gen_random_uuid() where id is null;
alter table public.daily_orders alter column id set default gen_random_uuid();
alter table public.daily_orders alter column id set not null;

-- CASCADE 必帶：deliveries_order_id_fkey、topic_requests_order_id_fkey 都 reference
-- daily_orders(id)，沒 CASCADE 會 cannot drop constraint ... because other objects
-- depend on it 整支 migration abort（fail-fast），後面 0025/0026 全沒跑。
alter table public.daily_orders drop constraint if exists daily_orders_pkey cascade;
alter table public.daily_orders add constraint daily_orders_pkey primary key (id);

-- 舊複合鍵仍是熱門查詢路徑（history 列表、逾時 reconcile），補一般 index。
create index if not exists idx_daily_orders_user_date
  on public.daily_orders (user_id, order_date desc, created_at desc);

-- DB 層唯一防線：同一 user 同一時間只能有一筆進行中訂單。第二筆進行中訂單
-- INSERT 時直接撞 UniqueViolation，router 層接成 409，不需要應用層鎖。
create unique index if not exists idx_daily_orders_one_active_per_user
  on public.daily_orders (user_id)
  where status in ('pending', 'queued');

-- ── 2. deliveries / topic_requests：補 order_id ──────────────────────
-- 解決「同一天多筆訂單時，哪筆交付屬於哪張訂單」的歧義——舊版 ready/
-- find_delivered_episode 都是用 (user_id, deliver_date) 猜的，多筆同天訂單
-- 下會直接猜錯。頻道路徑（daily_podcast/channel_plan）永遠不帶這個欄位，
-- NULL＝零行為變動。
alter table public.deliveries
  add column if not exists order_id uuid references public.daily_orders(id) on delete set null;
create index if not exists idx_deliveries_order
  on public.deliveries (order_id) where order_id is not null;

-- 擴大 unique constraint：0001_init.sql 原本是 unique (user_id, episode_id)，
-- 代表同一使用者對同一集數只能有一筆 delivery。佇列制下如果 reuse 命中一集
-- 使用者之前訂單已經收到的舊集數，第二筆訂單的 insert_delivery 會因為這個
-- constraint 衝突而 silently no-op、不會產生帶新 order_id 的新列，新訂單的
-- ready 判斷就永遠找不到自己的交付列而卡死。改成三欄唯一鍵：同一
-- (user, episode) 對不同 order 各自留一筆。
--
-- 刻意用 NULLS NOT DISTINCT（PG15+，這裡是 17.6）：Postgres 唯一索引預設把
-- NULL 視為互不相同，若不加這個修飾詞，頻道/evergreen 路徑（order_id 永遠
-- NULL）的重複 insert_delivery 呼叫會全部通過 unique 檢查、不再 dedup，
-- 直接破壞既有「同一 user 對同一集數只交付一次」的 heard-set 保證。加了
-- NULLS NOT DISTINCT 後，NULL 之間視為相等，頻道/evergreen 路徑的 dedup
-- 行為與改動前完全一致，只有帶了真實 order_id 的個人點餐路徑才會依 order_id
-- 分流出多筆。
alter table public.deliveries drop constraint if exists deliveries_user_id_episode_id_key;
-- ponytail: 第二次 deploy 時 0024 整支 fail-fast 在這行（commit 78adbe5 後
-- prod 已是 partial applied；add constraint 不支援 IF NOT EXISTS，唯一
-- idempotent 寫法是再 drop 一次。drop if exists 對「已 drop 過」的 constraint
-- 是 no-op，所以這條 line 對初次部署與重跑都安全，解決
-- 「2026-07-31 deploy 卡這行 / 0025/0026/0027 全部沒跑」的 prod 慘案）。
alter table public.deliveries drop constraint if exists deliveries_user_episode_order_key;
alter table public.deliveries
  add constraint deliveries_user_episode_order_key
  unique nulls not distinct (user_id, episode_id, order_id);

alter table public.topic_requests
  add column if not exists order_id uuid references public.daily_orders(id) on delete cascade;
create index if not exists idx_topic_requests_order
  on public.topic_requests (order_id) where order_id is not null;

-- ── 3. 拔掉 22:00 collect_open / 23:00 orchestrate 批次收單 cron ──────
-- 個人點餐已改成送出即觸發（POST /jobs/orders/{id}/generate）為主路徑，
-- 「隨時點餐」語意下批次收單不再需要，改用 order_reconcile 逾時兜底。
do $migration$
declare
  v_job_id bigint;
begin
  for v_job_id in
    select jobid from cron.job
    where jobname in ('dawncast-collect-open', 'dawncast-collect-close')
  loop
    perform cron.unschedule(v_job_id);
  end loop;
end
$migration$;

-- ── 4. 新增逾時兜底 cron：order_reconcile（每 5 分鐘）──────────────────
-- 即時觸發（fire-and-forget）可能失敗且不再有夜間批次補救，需要獨立的
-- 逾時兜底：pending 太久沒翻 queued（enqueue 失敗）就重放；queued 太久沒有
-- 任何 deliveries（生成真的掛了）就補一集常青集墊檔，避免使用者被卡住
-- 點不了下一餐。只服務個人點餐，不碰 channels/evergreen 既有邏輯。
select cron.schedule(
  'dawncast-order-reconcile',
  '*/5 * * * *',
  $cron$ select pgmq.send('control', jsonb_build_object('task', 'order_reconcile')) $cron$
);
