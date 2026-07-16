-- daily_orders.status 在 0001_init.sql 沒建 CHECK constraint，
-- pydantic Literal 與前端 Zod 是唯一防線；補上 DB 層保險。
-- 既有資料三個值都在白名單內，無須 backfill。
alter table public.daily_orders
  drop constraint if exists daily_orders_status_check;
alter table public.daily_orders
  add constraint daily_orders_status_check
  check (status in ('pending', 'queued', 'played'));