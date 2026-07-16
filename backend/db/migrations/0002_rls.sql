-- RLS 縱深防禦。授權主防線在 FastAPI（service-role 連線會 bypass RLS）；
-- 這層保護「萬一有人拿 anon/authenticated key 直連 PostgREST」的情況。
-- 每張 user 表都是同一個無聊模式：own rows（消除特殊情況）。

alter table public.users          enable row level security;
alter table public.deliveries     enable row level security;
alter table public.daily_orders   enable row level security;
alter table public.user_vocab     enable row level security;
alter table public.user_favorites enable row level security;
alter table public.user_settings  enable row level security;
alter table public.topic_requests enable row level security;
alter table public.episodes       enable row level security;
alter table public.dict_cache     enable row level security;

-- own rows：user_id = auth.uid()
create policy "own users"       on public.users          for all using (id = auth.uid()) with check (id = auth.uid());
create policy "own deliveries"  on public.deliveries     for all using (user_id = auth.uid()) with check (user_id = auth.uid());
create policy "own orders"      on public.daily_orders   for all using (user_id = auth.uid()) with check (user_id = auth.uid());
create policy "own vocab"       on public.user_vocab     for all using (user_id = auth.uid()) with check (user_id = auth.uid());
create policy "own favorites"   on public.user_favorites for all using (user_id = auth.uid()) with check (user_id = auth.uid());
create policy "own settings"    on public.user_settings  for all using (user_id = auth.uid()) with check (user_id = auth.uid());
create policy "own requests"    on public.topic_requests for all using (user_id = auth.uid()) with check (user_id = auth.uid());

-- episodes：免費集人人可讀；付費集只有有 delivery 的 owner 可讀
create policy "episodes readable" on public.episodes for select using (
  is_free = true
  or exists (
    select 1 from public.deliveries d
    where d.episode_id = episodes.id and d.user_id = auth.uid()
  )
);

-- dict_cache：全人可讀；只有 service_role 可寫（Edge/FastAPI 用 service key）
create policy "dict readable" on public.dict_cache for select using (true);
