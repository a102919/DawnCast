-- 0028: deliveries INSERT 自動翻牌 daily_orders.status='ready'（治本解）
--
-- 動機：commit 5272ca7 寫了 reconcile 的 promote_delivered_orders_to_ready 兜底，
-- 仍屬「狀態可被多條寫入路徑漏翻」的設計——任何未來加的寫入路徑或 partial commit
-- 都可能讓 delivery 已寫但 status 漏翻。reconcile 是 5 分鐘補牌，不是根本解。
--
-- 設計：AFTER INSERT trigger 把「翻牌」綁進 INSERT 本身，物理上不可能漏翻——
-- trigger 跟 INSERT 在同一個 transaction，commit / rollback 同步；任何未來
-- INSERT 進 deliveries 的新寫入路徑（即使繞過 deliver_and_mark_ready 直接 SQL）
-- 也自動安全。WHEN (NEW.order_id IS NOT NULL) 把頻道 / evergreen 路徑
-- （order_id 為 NULL）排除掉，不會誤觸翻牌。
--
-- 翻牌條件 status IN ('queued','expired') 對齊 deliver_and_mark_ready 既有語意：
-- pending 不翻（jobs trigger 還沒跑過就 INSERT 是異常路徑，留給 reconcile）；
-- played 不翻（使用者已標播放，遲到交付不該把狀態倒退回 ready）。
-- expired 會被翻回 ready——這是 deliver_and_mark_ready 的「遲到交付復活」語意，
-- reconcile 退役與單執行緒 worker 排隊可能讓 expire 早於 delivery 寫入，
-- trigger 復活可讓使用者真的拿得到內容。

create or replace function public.deliveries_flip_order_ready()
returns trigger as $$
begin
  update public.daily_orders
     set status = 'ready', updated_at = now()
   where id = NEW.order_id
     and status in ('queued', 'expired');
  return NEW;
end;
$$ language plpgsql;

drop trigger if exists deliveries_flip_order_ready on public.deliveries;
create trigger deliveries_flip_order_ready
  after insert on public.deliveries
  for each row
  when (NEW.order_id is not null)
  execute function public.deliveries_flip_order_ready();