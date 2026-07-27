-- 把 episodes.published_at 補上 default + backfill 歷史 NULL 列。
--
-- 背景：repo.upsert_episode 的 INSERT 沒有寫 published_at（repo.py:183-189），
-- schema 也沒 default，所以 worker 跑完一集落庫後 published_at 永遠是 NULL。
-- 後端 list API 用 coalesce(to_char(published_at, 'YYYY-MM-DD'), '') 變空字串，
-- 前端 formatDateZhTW('') fallback 回原文 → 卡片 metadata 那行什麼都沒顯示。
--
-- 修法兩段：
--   1. ALTER COLUMN ... SET DEFAULT current_date：新集落庫自動帶生成當日，
--      不需要動 Python（最小修改、不引入新分支）。
--   2. UPDATE 把歷史 NULL 列補上 created_at::date — 這是事實上線時間，
--      沒有更準的欄位可用。批次跑一次就夠。
--
-- 冪等：default 設固定值冪等；backfill 用 WHERE is null 守門，重跑零效果。
--
-- 不動 list_episodes / list API / EpisodeListItem：default 上線後，
-- coalesce(to_char(..., 'YYYY-MM-DD'), '') 自動解開成真實日期字串，
-- 前端 zod 與 formatDateZhTW 都不需要改。

alter table public.episodes
  alter column published_at set default current_date;

update public.episodes
   set published_at = created_at::date
 where published_at is null;