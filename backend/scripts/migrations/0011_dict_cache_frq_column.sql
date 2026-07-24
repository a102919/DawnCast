-- 補 dict_cache 的 frq 欄位 + partial index（worker dict_translate queue backfill 用）。
--
-- 歷史：本地 DB 在某次 backfill 後多了 frq integer 欄位（單字使用頻率，給 worker
-- 排程優先順序背書）跟 idx_dict_cache_missing_frq partial index（只索引 example_en 缺漏的列，
-- 縮小 backfill queue 掃描範圍）。Prod schema 缺這兩個，會讓 worker pipeline 排程時拿到 NULL frq。
--
-- 與 0010 dict_cache_example_columns.sql 互補：0010 補 example_en/example_zh 給 /vocab 顯示用；
-- 本檔補 frq + partial index 給 worker 排程用。
--
-- 同步鏡像動作（一次性）：
--   1. 在 db-pran marketplace 開 public TCP port forwarding（5432）
--   2. Mac 直接用 psycopg COPY stream 本地 dict_cache 1.79M 列到 prod
--   3. 同步完成後 disable forwarding，避免 5432 對外長開
--   4. 寫進 schema migration 讓後續 deploy / rebuild worker-gir 時 schema 對齊
--
-- Migration 是冪等的（IF NOT EXISTS），可以重跑。

alter table public.dict_cache
  add column if not exists frq integer;

create index if not exists idx_dict_cache_missing_frq
    on public.dict_cache (frq)
    where example_en is null or example_en = '';
