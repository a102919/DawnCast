-- daily_orders.status 在 0001_init.sql 沒建 CHECK constraint，
-- pydantic Literal 與前端 Zod 是唯一防線；補上 DB 層保險。
-- 既有資料三個值都在白名單內，無須 backfill。
--
-- ponytail: apply_migrations 每次部署都從 0001 全部重跑一次（idempotent
-- 設計，見 scripts/apply_migrations.py），這支不是「跑一次就過去」而是
-- 每次部署都要對「當下」的 daily_orders 資料重新驗證。0025/0027 後續
-- 把白名單擴大到 ready/expired，一旦 prod 出現這些狀態的資料列，這裡若
-- 還維持窄白名單，重跑就會在 ADD CONSTRAINT 檢查現有資料時直接 fail-fast，
-- 擋住 0009 之後所有 migration（2026-08-05 prod 事故：擋到新增的 0029）。
-- 直接跟最終狀態（0027）同步白名單，之後不會再因為資料早就超前而重跑失敗。
alter table public.daily_orders
  drop constraint if exists daily_orders_status_check;
alter table public.daily_orders
  add constraint daily_orders_status_check
  check (status in ('pending', 'queued', 'ready', 'played', 'expired'));