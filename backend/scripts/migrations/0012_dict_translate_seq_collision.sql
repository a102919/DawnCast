-- 修 dict_translate queue 的 BIGSERIAL 與 a_dict_translate 撞鍵問題。
--
-- 根因：dict_translate queue 之前被 drop + recreate 過（pgmq drop queue 不會清 a_ 表），
-- 新 q 的 BIGSERIAL 從 1 開始，但 a_dict_translate 還留著舊 incarnation 的 archived row
-- （msg_id 1-3963，enqueued_at 2026-07-12），所以新送的訊息 msg_id 1-15 全部撞到。
--
-- 修法：
-- 1. 推進 q_dict_translate_msg_id_seq 跳過 3964 之後，新 send 就不會再撞。
-- 2. 從 q 刪除 msg_id <= 15 的 row（這 14 筆已在 a_ 內，刪 q 是 no-op）。
--    這 14 筆是 stuck dead-letter（read_ct 238-242），即使重試也只會再撞 UniqueViolation。
--    對應的 word（ai agent、offshore、hbm、supply chain 等）已在 2026-07-12 archive，
--    翻譯結果已落地 dict_cache（如有）或永久丟失，可日後手動 backfill。
--
-- 沒動 dict_translate.py code：bug 是「drop queue 不清 a_」的 pgmq 行為，
-- 不在 DawnCast 控制範圍。ponytail: 升 pgmq > 1.x 看有沒有 drop-archive-cascade 選項，
-- 否則未來 drop+recreate 任何 queue 都會撞同樣問題（記憶體備忘：migration 加 ponytail note）。

-- 1. bump sequence past existing a_ rows
select setval(
    'pgmq.q_dict_translate_msg_id_seq'::regclass,
    greatest(
        (select coalesce(max(msg_id), 0) from pgmq.a_dict_translate),
        (select last_value from pgmq.q_dict_translate_msg_id_seq)
    )
);

-- 2. clear stuck q rows that are already in a_
delete from pgmq.q_dict_translate
where msg_id <= (select max(msg_id) from pgmq.a_dict_translate)
  and exists (select 1 from pgmq.a_dict_translate a where a.msg_id = pgmq.q_dict_translate.msg_id);
