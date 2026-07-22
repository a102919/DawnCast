-- 單字本來源句子的中文對照：加入時 activeCue.zh 早就存在（逐字稿強制中英對照），
-- 只是先前沒存。補這欄，讓「來源句子」不再只有英文。
alter table public.user_vocab add column if not exists source_sentence_zh text;
