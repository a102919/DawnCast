-- 補 dict_cache 的 mnemonic 欄位（諧音/關鍵字記憶提示，台灣在地化記憶法）。
--
-- 給台灣漢字母語學習者的文字型記憶聯想（例：「發音像『OO』，聯想到＿＿」），
-- 由既有的 translate_word/translate_batch LLM 呼叫順便產生，不新增 LLM call。
--
-- 懶惰 backfill：舊字不做一次性全補，下次被 /dict/lookup 或 dict_translate
-- worker 翻譯到時自然補上，跟 0010/0011 補 example/frq 欄位同一套做法。
--
-- Migration 是冪等的（IF NOT EXISTS），可以重跑。

alter table public.dict_cache
  add column if not exists mnemonic text;
