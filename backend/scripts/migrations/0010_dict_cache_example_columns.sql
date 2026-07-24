-- 補 dict_cache 的字典例句欄位。
--
-- 歷史：0001_init.sql 建表時只有 word/ipa/pos/translation/exchange/audio_url/created_at，
-- 但 routers/dict.py insert 與 routers/vocab.py select 都已經引用 example_en/example_zh。
-- 跑這些 endpoint 會 500 internal_error。
--
-- 補上欄位為 nullable text（舊資料不需要 backfill，例句從 worker pipeline 寫入時自然帶上）。

alter table public.dict_cache
  add column if not exists example_en text,
  add column if not exists example_zh text;
