-- T2：學習進度 / Activity 上雲。單列存 4 個累積型 jsonb 欄位 + 播放進度快照。
-- PATCH 的合併語意（去重/上限/遞增）在 app 層（app/routers/activity.py）用 Python
-- 純函式處理，這裡只存合併後的最終值，SQL 維持跟 user_settings 一樣無聊的
-- insert...on conflict 寫法，不引入 jsonb `||`/`jsonb_set` 等原子運算。

create table if not exists public.user_activity (
  user_id                 uuid primary key references public.users(id) on delete cascade,
  streak_dates            jsonb not null default '[]',   -- ["YYYY-MM-DD", ...]，去重、排序後上限 365 筆
  listen_minutes          jsonb not null default '{}',   -- {"YYYY-MM": minutes}
  lookup_count            jsonb not null default '{}',   -- {"YYYY-MM": count}
  listened_episode_ids    jsonb not null default '[]',   -- 已聽完（>=80%）的集數 id，去重
  last_played_episode_id  text,
  last_played_position    double precision,
  last_played_at          timestamptz,
  updated_at              timestamptz not null default now()
);

alter table public.user_activity enable row level security;

-- ponytail: drop policy if exists 在 create 前 — 配合 apply_migrations 重跑
-- idempotent 需求（沒 schema_migrations 表）。見 0002_rls.sql 開頭註解。
drop policy if exists "own activity" on public.user_activity;
create policy "own activity" on public.user_activity
  for all using (user_id = auth.uid()) with check (user_id = auth.uid());
