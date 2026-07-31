-- 0027: daily_orders 新增 'expired' 狀態（治本解：卡死的 active 訂單不再永遠卡 UI）
--
-- 動機：原本 active = status IN ('pending','queued')，沒有 upper bound。
-- reconcile 重試與 evergreen fallback 都能失敗（worker.py:218-220 找不到常青集
-- 直接 continue），row 一旦卡死就永遠從 GET /active 回傳，使用者畫面永遠
-- 「這集正在生成中」、點播下一集永遠 disabled。
--
-- 設計：worker._order_reconcile 改為每 5 分鐘掃一次「active 太久且沒 delivery」
-- → CAS 翻 expired。expired 不算 active（one-active-per-user index 自然放行），
-- 也會出現在 /history（讓使用者看到「這集被放棄了」而不是憑空消失）。
-- 30 分鐘上限 ≫ STUCK_PENDING_SEC=120 + STUCK_QUEUED_SEC=1200 = ~22 分鐘
-- 正常 SLA，給 reconcile 至少兩輪完整重試 + 一輪 evergreen 兜底。

alter table public.daily_orders
  drop constraint if exists daily_orders_status_check;
alter table public.daily_orders
  add constraint daily_orders_status_check
  check (status in ('pending', 'queued', 'ready', 'played', 'expired'));

-- partial unique index 不動：expired 不在 ('pending','queued') 集合，
-- expired 不再佔 active 槽位，下一筆點餐可以正常 INSERT（0024 已定義）。