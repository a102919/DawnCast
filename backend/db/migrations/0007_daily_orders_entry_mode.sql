-- Podcast 生成流程重新設計 §6（Phase 4）：把入口類型與長度 tier 從 daily_orders
-- 一路帶到 topic_requests、resolve_for_user、find_reusable_episode。
-- 三欄皆有預設值，向下相容既有 daily_orders 列（視為 topic/medium）。
--
-- entry_mode 字面值與 Phase 1 既有 TopicType Literal 對齊：news/product/evergreen/skill。
-- 前端入口 UI 對映 short user-visible 三選一：news/topic/knowledge（skill 暫不開給使用者）。
alter table public.daily_orders
  add column if not exists entry_mode  text not null default 'topic',
  add column if not exists length_tier text not null default 'medium';

-- topic_requests：length_tier 是新欄；topic_type 既有欄位從 NULL 變可寫。
-- 既有 0001_init.sql 已建好 topic_type 欄（一直沒 caller 在寫），現在 orchestrate 會帶入。
alter table public.topic_requests
  add column if not exists length_tier text not null default 'medium';
