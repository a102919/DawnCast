-- 拿掉「播放完才能點下一筆」的限制，改成「生成完成就能點下一筆」。
-- 新增 status='ready' 中繼態：queued（生成中）→ ready（生成完成，可收聽，
-- 已解鎖下一筆）→ played（使用者實際播放完）。
-- idx_daily_orders_one_active_per_user（migration 0024）只擋
-- status in ('pending','queued')，ready 不在裡面，不用動索引。
alter table public.daily_orders
  drop constraint if exists daily_orders_status_check;
alter table public.daily_orders
  add constraint daily_orders_status_check
  check (status in ('pending', 'queued', 'ready', 'played'));
