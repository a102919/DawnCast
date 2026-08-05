-- 拿掉「播放完才能點下一筆」的限制，改成「生成完成就能點下一筆」。
-- 新增 status='ready' 中繼態：queued（生成中）→ ready（生成完成，可收聽，
-- 已解鎖下一筆）→ played（使用者實際播放完）。
-- idx_daily_orders_one_active_per_user（migration 0024）只擋
-- status in ('pending','queued')，ready 不在裡面，不用動索引。
--
-- ponytail: 同 0008 的教訓——這裡本來只白名單到 played，0027 才加
-- expired，但 apply_migrations 每次部署都從頭重跑全部檔案，一旦 prod
-- 已有 expired 資料，重跑到這支就會在 ADD CONSTRAINT 檢查現有資料時
-- fail-fast、擋住 0026 之後的 migration。直接跟最終狀態（0027）同步。
alter table public.daily_orders
  drop constraint if exists daily_orders_status_check;
alter table public.daily_orders
  add constraint daily_orders_status_check
  check (status in ('pending', 'queued', 'ready', 'played', 'expired'));
