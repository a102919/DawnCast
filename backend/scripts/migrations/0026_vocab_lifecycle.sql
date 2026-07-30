-- 0026: 單字生命週期 2.0
-- status 語意重整：1=新字(待學習) 2=複習中(SRS) 3,4=保留 5=精熟封存
-- （0001 的註解「1=new..5=ignored」自此以本檔為準；畢業候選＝status 2 且
--   interval_days >= 21 且到期，純推導不落 DB。）

alter table public.user_vocab
  add column if not exists quiz_pass_streak smallint not null default 0;

-- backfill：曾複習過的既有卡片視為「複習中」，避免整批被丟回學習模式。
-- 判定「曾複習過」：sm2 任一 quality 都會讓 interval 或 ease 偏離初始值
-- （q=1→ease 1.96、q=3/4/5→interval 6）；updated_at 條件是保險帶
-- （user_vocab 只有 PATCH 會碰 updated_at）。
update public.user_vocab
   set status = 2
 where status = 1
   and (interval_days is distinct from 1
        or ease is distinct from 2.5
        or updated_at > created_at + interval '1 minute');
