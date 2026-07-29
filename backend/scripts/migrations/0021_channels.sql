-- 頻道（Channel）機制地基層：頻道本體、選題庫、使用者訂閱、episodes 掛勾。
--
-- 設計重點：
--   1. channels：一個頻道＝一種穩定的內容定位。theme_prompt 是給選題 LLM 的
--      系統提示（非對外文案）；target_interval_days 是這個頻道自訂的出片節奏，
--      搭配 last_published_at 讓排程端（見 0022 的 pick_daily_topics）算出
--      「該不該催」的飢餓因子，不必另外建 cron 排程表。
--   2. channel_topics：選題庫（candidate pool）。選題 LLM 一次生一批候選存
--      這裡，跟「今天要不要生」解耦——生成排程只從這裡挑，選題可以超前執行、
--      獨立於每日批次。unique (channel_id, lower(canonical_topic)) 讓同頻道
--      候選天然去重，選題 LLM 重複建議同一主題時 INSERT ... ON CONFLICT DO
--      NOTHING 直接吃掉，不需要應用層先查再插。
--   3. parent_episode_id 與 episode_id 分開兩欄：前者是「這個選題是接續哪一集
--      的系列企劃」（規劃階段就知道），後者是「生成完成後實際產出的集數」
--      （回填階段才有值），語意不同不能共用一欄。
--   4. user_channel_subscriptions 只是關聯表，不影響生成排程——生成節奏是
--      頻道自己的事（target_interval_days），訂閱只決定誰在收件匣看得到通知，
--      兩件事刻意分開，不要互相耦合。
--   5. episodes.channel_id：nullable + on delete set null——沒掛頻道的舊集
--      （daily_batch 固定 slot、使用者點餐集）完全不受影響；頻道被刪除也不會
--      連坐砍掉已經生成好的集數，只是它們變回「無頻道」的孤兒集。
--
-- 前置條件：0001_init.sql 已建立 pgcrypto extension（gen_random_uuid）與
-- public.episodes / public.users。

create table if not exists public.channels (
  id                   uuid primary key default gen_random_uuid(),
  slug                 text unique not null,
  name                 text not null,
  description          text,
  theme_prompt         text not null,                     -- 給選題 LLM 的頻道定位
  topic                text not null,                     -- tech|business|culture|science
  topic_type           text not null default 'evergreen', -- news|product|evergreen|skill
  length_tier          text not null default 'medium',
  cefr_level           text not null default 'B1',
  target_interval_days smallint not null default 3,
  status               text not null default 'active',    -- active|paused|archived
  cover_r2_key         text,
  last_published_at    date,
  created_at           timestamptz not null default now(),
  updated_at           timestamptz not null default now()
);

comment on table public.channels is
  '頻道本體：一個穩定的內容定位（主題 + 難度 + 出片節奏）。theme_prompt 只給'
  '選題 LLM 當系統提示，不對使用者曝光（見 ChannelPublic 契約）。';

create table if not exists public.channel_topics (
  id                uuid primary key default gen_random_uuid(),
  channel_id        uuid not null references public.channels(id) on delete cascade,
  canonical_topic   text not null,
  angle             text not null,
  rationale         text,
  score             real not null default 0,
  status            text not null default 'candidate',  -- candidate|scheduled|published|rejected|stale
  parent_episode_id uuid references public.episodes(id) on delete set null,
  episode_id        uuid references public.episodes(id) on delete set null,
  created_at        timestamptz not null default now(),
  decided_at        timestamptz
);
create unique index if not exists uq_channel_topics_norm
  on public.channel_topics (channel_id, lower(canonical_topic));
create index if not exists idx_channel_topics_pick
  on public.channel_topics (channel_id, status, score desc);

comment on table public.channel_topics is
  '頻道選題庫（candidate pool）：選題 LLM 產生候選，生成排程（pick_daily_topics）'
  '再依 score／飢餓因子挑要生的那幾筆。unique (channel_id, lower(canonical_topic))'
  '讓重複建議的主題在 INSERT 階段就被 ON CONFLICT DO NOTHING 吃掉。';

create table if not exists public.user_channel_subscriptions (
  user_id    uuid not null references public.users(id) on delete cascade,
  channel_id uuid not null references public.channels(id) on delete cascade,
  created_at timestamptz not null default now(),
  primary key (user_id, channel_id)
);

comment on table public.user_channel_subscriptions is
  '使用者訂閱哪些頻道；純關聯表，不影響生成排程（生成節奏由 channels.'
  'target_interval_days 決定，訂閱只決定誰在收件匣／通知看得到新集）。';

alter table public.episodes
  add column if not exists channel_id uuid references public.channels(id) on delete set null;
create index if not exists idx_episodes_channel on public.episodes (channel_id, episode_no desc);
