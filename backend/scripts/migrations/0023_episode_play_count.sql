-- 單集播放次數計數。ponytail: 只存總數不存事件，要做時間序列或不重複聽眾
-- 再上 episode_plays 事件表；現在的規模一個 integer 就回答得了問題。
alter table public.episodes
  add column if not exists play_count integer not null default 0;
