-- DawnCast 初始 schema
-- PRD §4（六張引擎核心表）+ §7.5.4（單字本）+ 前端 Api 契約收斂。
-- 授權主防線在 FastAPI（驗 JWT → user_id 收斂查詢）；RLS 開啟當縱深防禦。
-- pgvector 起步不建 ivfflat/hnsw（資料量小，精確掃描更穩，PRD §3.1）。

create extension if not exists vector;
create extension if not exists pgcrypto;   -- gen_random_uuid

-- ─────────────────────────────────────────────────────────────
-- users：對接 Supabase auth.users（id 同步）
-- ─────────────────────────────────────────────────────────────
create table if not exists public.users (
  id                   uuid primary key references auth.users(id) on delete cascade,
  tz                   text not null default 'Asia/Taipei',
  delivery_time        time not null default '07:00',
  onboarding_big_topic text,
  cefr_target          text not null default 'B1',
  created_at           timestamptz not null default now()
);

-- ─────────────────────────────────────────────────────────────
-- episodes：元資訊（episodeData.ts）+ 內容（episode.json）+ PRD §4.4 引擎欄位
-- ─────────────────────────────────────────────────────────────
create table if not exists public.episodes (
  id                uuid primary key default gen_random_uuid(),
  slug              text unique not null,        -- 對外 id（前端契約用 slug）
  title             text not null,
  title_zh          text,
  topic             text not null,               -- tech|business|culture|science
  cefr_level        text not null default 'B1',
  is_free           boolean not null default false,
  is_featured       boolean not null default false,
  episode_no        int,
  published_at      date,
  script_json       jsonb,                       -- cues[]：{index,speaker,text,zh,start,end}
  audio_r2_key      text,
  mp4_r2_key        text,
  srt_r2_key        text,
  -- PRD §4.4 引擎欄位
  extracted_facts   jsonb,
  target_vocab      jsonb,
  big_topic         text,
  variant_no        int not null default 1,
  angle             text,                        -- 定義/人物故事/常見誤解/應用場景/歷史/對比
  freshness_class   text not null default 'evergreen',  -- evergreen/timely/dated
  expires_at        timestamptz,
  topic_vec         vector(512),
  content_vec       vector(512),
  source_cluster_id uuid,
  embedding_model_version text,
  -- 冪等鍵：夜間生成重投時靠它復用同一列，避免重複建集 / R2 孤兒物件。
  -- unique 允許多個 NULL（seed / 手動匯入的集不帶 key），故不影響既有資料。
  idempotency_key   text unique,
  created_at        timestamptz not null default now()
);
create index if not exists idx_episodes_topic on public.episodes (topic, published_at desc);
create index if not exists idx_episodes_free  on public.episodes (is_free) where is_free = true;
create index if not exists idx_episodes_big_topic on public.episodes (big_topic);

-- ─────────────────────────────────────────────────────────────
-- deliveries：PRD §4.5 — 交付 + heard-set 權威來源 + 授權依據
-- ─────────────────────────────────────────────────────────────
create table if not exists public.deliveries (
  id           uuid primary key default gen_random_uuid(),
  user_id      uuid not null references public.users(id) on delete cascade,
  episode_id   uuid not null references public.episodes(id) on delete cascade,
  deliver_date date not null,
  heard        boolean not null default false,
  heard_at     timestamptz,
  position_sec int not null default 0,
  unique (user_id, episode_id)
);
create index if not exists idx_deliveries_user on public.deliveries (user_id, deliver_date desc);

-- ─────────────────────────────────────────────────────────────
-- daily_orders：前端 UX 表，精確對映 frontend DailyOrder 契約
-- 夜間由 SQL 投影成 topic_requests（落差消化在投影那一步）
-- ─────────────────────────────────────────────────────────────
create table if not exists public.daily_orders (
  user_id          uuid not null references public.users(id) on delete cascade,
  order_date       date not null,
  selected_topics  jsonb not null default '[]',
  specific_request text,
  status           text not null default 'pending',  -- pending/queued/played
  delivery_time    time not null default '07:00',
  created_at       timestamptz not null default now(),
  updated_at       timestamptz not null default now(),
  played_at        timestamptz,
  primary key (user_id, order_date)
);

-- ─────────────────────────────────────────────────────────────
-- user_vocab：PRD §7.5.4 + 前端 VocabItem 的 SM-2 欄位
-- ─────────────────────────────────────────────────────────────
create table if not exists public.user_vocab (
  id                uuid primary key default gen_random_uuid(),
  user_id           uuid not null references public.users(id) on delete cascade,
  word              text not null,
  lemma             text not null,
  pos               text,
  translation       text not null,
  ipa               text,
  sense_idx         smallint not null default 0,
  source_episode_id uuid references public.episodes(id) on delete set null,
  source_line_no    int,
  source_timestamp  real,
  source_sentence   text,
  -- SM-2（前端契約 nextReview/interval/ease）
  next_review       date,
  interval_days     int default 1,
  ease              real default 2.5,
  status            smallint not null default 1,   -- 1=new..5=ignored
  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now(),
  unique (user_id, lemma, source_episode_id, source_line_no)
);
create index if not exists idx_user_vocab_user   on public.user_vocab (user_id, created_at desc);
create index if not exists idx_user_vocab_review on public.user_vocab (user_id, next_review)
  where status between 1 and 3;

-- ─────────────────────────────────────────────────────────────
-- user_favorites / user_settings / dict_cache
-- ─────────────────────────────────────────────────────────────
create table if not exists public.user_favorites (
  user_id    uuid not null references public.users(id) on delete cascade,
  episode_id uuid not null references public.episodes(id) on delete cascade,
  created_at timestamptz not null default now(),
  primary key (user_id, episode_id)
);

create table if not exists public.user_settings (
  user_id               uuid primary key references public.users(id) on delete cascade,
  popup_enabled         boolean not null default true,
  popup_dont_show_again boolean not null default false,
  playback_rate         real not null default 1,
  font_size             text not null default 'md',
  theme                 text not null default 'auto',
  preferred_topics      jsonb not null default '[]',
  default_delivery_time time not null default '07:00',
  updated_at            timestamptz not null default now()
);

create table if not exists public.dict_cache (
  word        text primary key,        -- lowercase
  ipa         text,
  pos         jsonb not null default '[]',
  translation text not null,
  exchange    text,
  audio_url   text,
  created_at  timestamptz not null default now()
);

-- ─────────────────────────────────────────────────────────────
-- PRD §4.2/4.3/4.6 引擎表（MVP 建表；向量聚類邏輯留骨架，不接主流程）
-- ─────────────────────────────────────────────────────────────
create table if not exists public.topic_requests (
  id              uuid primary key default gen_random_uuid(),
  user_id         uuid not null references public.users(id) on delete cascade,
  request_date    date not null,
  raw_topic       text,
  canonical_topic text,
  topic_type      text,                 -- news/product/evergreen/skill
  topic_vec       vector(512),
  cluster_id      uuid,
  source          text not null default 'specified',  -- specified/fallback
  created_at      timestamptz not null default now()
);
create index if not exists idx_topic_requests_date on public.topic_requests (request_date);

create table if not exists public.topic_clusters (
  id                  uuid primary key default gen_random_uuid(),
  cluster_date        date not null,
  centroid_vec        vector(512),
  canonical_topic     text,
  big_topic           text,
  member_count        int not null default 0,
  resolved_episode_id uuid references public.episodes(id)
);

create table if not exists public.user_heard_topics (
  user_id     uuid not null references public.users(id) on delete cascade,
  topic_vec   vector(512),
  content_vec vector(512),
  episode_id  uuid references public.episodes(id) on delete set null,
  heard_date  date not null
);
create index if not exists idx_user_heard_topics_user on public.user_heard_topics (user_id, heard_date desc);

-- ─────────────────────────────────────────────────────────────
-- 新用戶 trigger：auth.users 新建 → 自動補 users + user_settings
-- 讓前端 getSettings 永遠撈得到列（消除特殊情況）
-- ─────────────────────────────────────────────────────────────
create or replace function public.handle_new_user() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  insert into public.users (id) values (new.id) on conflict do nothing;
  insert into public.user_settings (user_id) values (new.id) on conflict do nothing;
  return new;
end; $$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();
