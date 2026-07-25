-- 為 episodes 補 sources JSONB 欄位，儲存 retrieve_sources_node 抓到的真實資料片段。
--
-- 設計重點：
--   1. 欄位型別 jsonb not null default '[]'::jsonb：向後相容（既有列補空陣列）。
--   2. API 對外只暴露 references（SourceReference = {id, title, url}），不暴露 text。
--      但 DB 落原文（text）以利未來 audit / 重新生成時直接複用。
--   3. URL 安全過濾在 app 層做（router.episodes）：只取 http/https 開頭的 URL，
--      防 javascript:/data:/file: 等 XSS / SSRF 風險進入前端。
--
-- 前置條件：0001_init.sql 已建立 public.episodes。
--
-- 不在此層做 source URL 白名單：URL 形狀由各 SourceProvider 決定（wikipedia / tavily /
-- gdelt），落庫時僅做長度限制避免 JSONB 爆量。

alter table public.episodes
  add column if not exists sources jsonb not null default '[]'::jsonb;

comment on column public.episodes.sources is
    'retrieve_sources_node 抓到的 SourceSnippet 清單；前端只取 references（http/https URL 過濾在 router）。';

-- 預期結構（shape）：[{id, title, url, text, published_at}, ...]
-- 落原文是刻意的：避免重抓成本，也讓重新生成時可複用。
-- 若未來要對 text 做全文索引，加 GIN index 列在後續 migration。