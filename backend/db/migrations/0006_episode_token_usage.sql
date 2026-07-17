-- 記錄單集寫稿 + judge 全部 LLM 呼叫的 token 用量，供成本核算。
-- 一集一組總量（非逐次呼叫明細，明細留在 log），跟 episodes 1:1，比照 0005 的做法直接加欄位。
alter table public.episodes add column if not exists input_tokens integer not null default 0;
alter table public.episodes add column if not exists output_tokens integer not null default 0;
