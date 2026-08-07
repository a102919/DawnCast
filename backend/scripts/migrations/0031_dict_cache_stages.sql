-- dict_cache 精煉改成多階段：義項、諧音、核心語意複審各跑一趟（見 refine_dict_senses.py）。
--
-- 為什麼要分階段：三個產出對 LLM 的要求互相矛盾。senses/例句要收斂（低溫、
-- 嚴格格式），諧音聯想要發散（高溫、每個字不一樣），核心語意要的是「回頭審視」
-- 而不是「生成」。0030 那版把三件事塞進同一次呼叫，結果諧音欄位塌成單一模板
-- （1869 筆裡 1387 筆開頭一模一樣「發音像『X』，聯想到『Y』」），連 the / of / and
-- 都生了一條——這種字根本不該有諧音提示。
--
-- quality 保持原義（0=ECDICT 原始 1=LLM 精煉 2=人工確認），stages 記「跑過哪幾趟」，
-- 兩者正交：quality 是資料可信度，stages 是流程進度。
alter table public.dict_cache
  add column if not exists stages smallint not null default 0;

comment on column public.dict_cache.stages is
  'bitmask：1=義項精煉 2=諧音 4=核心語意複審（scripts/refine_dict_senses.py --job）';

-- 已精煉過的資料補上義項階段旗標，才不會被新的 --job refine 重撈一次。
update public.dict_cache set stages = stages | 1 where quality >= 1 and (stages & 1) = 0;

-- 0030 產出的諧音全數作廢：它們是同一個模板的變體，留著只會讓 --job mnemonic
-- 以為這些字已經有提示了。清空後由 --job mnemonic 依門檻重生（多數字會維持 null）。
update public.dict_cache set mnemonic = null
where quality = 1 and mnemonic is not null;

-- 核心語意寫成詞源解釋的（box →「四面封閉的空間，從盒子延伸為打拳的方場」）先就地清掉；
-- 剩下的交給 --job review 逐筆判斷有沒有硬湊。
update public.dict_cache set core_sense = null
where quality = 1
  and core_sense is not null
  and (char_length(core_sense) > 12
       or core_sense like '%延伸為%' or core_sense like '%延伸出%'
       or core_sense like '%亦指%'   or core_sense like '%引申%');

-- 義項對應詞過長的字（zh 超過 6 字）退回 quality = 0 重新精煉：
-- 這欄是卡片正面的主體，不能只是清掉了事，得用新 prompt 重生。
update public.dict_cache set quality = 0, stages = 0
where quality = 1
  and exists (
    select 1 from jsonb_array_elements(senses) s
    where char_length(s->>'zh') > 6
  );

create index if not exists idx_dict_cache_stages on public.dict_cache (stages) where quality >= 1;
