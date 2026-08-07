-- 0030: dict_cache 義項化
-- ECDICT 匯入的 translation 是整坨字典 dump（run 有 30 個義項擠在一個 text
-- 欄位、用字面 \n 分隔），無論前端怎麼排版都救不了。改由 LLM 精煉成結構化義項。
--
-- senses     : [{"pos": "vt.", "zh": "經營、管理"}, ...] 依頻率排序，最多 4 筆。
--              zh 是精簡對應詞（≤6 字、不堆逗號），不放英英定義、不放同義詞。
-- core_sense : 多義字的認知錨點（run → 「持續的流動或運作」），單義字留 null。
-- quality    : 0=ECDICT 原始 1=LLM 精煉 2=人工確認。
--
-- quality 存在的理由是 dict_translate 的 upsert 寫死
-- 「translation <> '' 就保留既有值」，爛翻譯永遠蓋不掉；精煉批次靠
-- `where quality = 0` 才進得去，也讓重跑天然冪等。

alter table public.dict_cache
  add column if not exists senses     jsonb,
  add column if not exists core_sense text,
  add column if not exists quality    smallint not null default 0;

-- 精煉批次的取件索引：只掃還沒精煉過的列。frq 是排序鍵（低 = 高頻優先）。
create index if not exists idx_dict_cache_unrefined
  on public.dict_cache (frq)
  where quality = 0;
