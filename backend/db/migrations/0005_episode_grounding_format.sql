-- Podcast 生成流程重新設計：長度 tier / 格式（單人口白 vs 雙主持）/ 是否有真實資料
-- grounding。三欄皆有預設值，向下相容既有列（視為 medium/dialogue/未 grounded）。
alter table public.episodes add column if not exists length_tier text not null default 'medium';
alter table public.episodes add column if not exists format text not null default 'dialogue';
alter table public.episodes add column if not exists grounded boolean not null default false;
