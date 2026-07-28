-- 新增 episodes.audio_r2_keys jsonb 給「每行一個 mp3」新路徑用。
--
-- 背景：舊路徑用 audio_r2_key text 存整集 episode.mp3 的 R2 key，後端
-- ffmpeg concat → libmp3lame 重編碼 → 等比縮放字幕時長，導致字幕飄
-- 0.5–2s。新路徑改為逐行 mp3 各自上 R2，前端 Web Audio API 串接播，
-- 字幕與音檔數學上對齊。
--
-- 決策：保留 audio_r2_key 不 drop，原因：
--   1. 既有 26+ 集已經用 audio_r2_key 指向整集 mp3 R2 key，backfill
--      需要從這些 key 下載整集 → ffmpeg 切段 → 上傳 segments
--   2. audio_r2_key 在 repo / routers / admin / tests 多處引用，
--      同一次 migration 改動太多檔案風險高
--   3. 兩欄可並行：新集只寫 audio_r2_keys；舊集 backfill 完成後再 drop
--
-- 冪等：add column with default '[]' 是冪等的（重跑 IF NOT EXISTS 守門）。
-- audio_r2_keys 寫入由 update_episode_keys 一次性寫 jsonb list，不需 partial。

alter table public.episodes
  add column if not exists audio_r2_keys jsonb not null default '[]'::jsonb;
