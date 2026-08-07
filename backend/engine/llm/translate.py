"""LLM 翻譯：輕量級 util，不污染 engine Protocol。

dict_translate worker 與 /dict/lookup fallback 共用。
直接走 MiniMax Anthropic 相容 endpoint（與 podcast 生成同一服務、同一帳號）。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx

from shared.config import Settings, get_settings
from shared.errors import GenerationError

logger = logging.getLogger(__name__)

_BATCH_MAX_TOKENS = 8192  # 10 字冷僻字實測 6808 tokens（thinking + output），留 buffer
_BATCH_READ_TIMEOUT = 180.0  # 10 字冷僻字實測 80s，留 2x buffer；覆寫 settings.http_read_timeout

# translate_word / translate_batch 共用的翻譯品質規則，兩邊各自在前後加自己專屬的規則
# （batch 版多一條 word echo 規則、輸出格式規則措辭也不同），組裝時用 enumerate 統一編號。
_TRANSLATION_RULES: tuple[str, ...] = (
    "translation 必須繁體中文台灣用詞（網路/網路、磁碟/磁碟、滑鼠/滑鼠）。",
    "example_en 要自然、簡短（≤ 15 字），能展示該字典型用法。",
    "example_zh 為 example_en 的逐字台繁翻譯。",
    "mnemonic 預設填 null；只有 ≥6 個字母、意思具體可想像的字才生"
    "（功能詞、代名詞、介系詞、抽象概念一律 null）。≤30 字繁體中文，"
    "不要套固定句型，尤其不要寫成「發音像『X』，聯想到『Y』」。",
)


def _numbered_rules(rules: list[str]) -> str:
    return "\n".join(f"{i}. {r}" for i, r in enumerate(rules, start=1))


def _resolve_llm_creds(settings: Settings) -> tuple[str, str, str]:
    """依 generation_engine 選 (base_url, auth_token, model)，跟 chat.make_langchain_chat 對齊。

    之前這裡永遠讀 minimax_auth_token，production 用 GENERATION_ENGINE=api_key
    時該欄位是空的，導致 dict_translate 100% 401。
    """
    if settings.generation_engine == "api_key":
        return settings.api_base_url, settings.api_key, settings.api_model
    return settings.minimax_anthropic_base_url, settings.minimax_auth_token, settings.minimax_model


async def _call_minimax(payload: dict[str, Any], read_timeout: float) -> dict[str, Any]:
    """打 MiniMax `/v1/messages`，回傳 {"text": <content blocks 抽出的文字>}。

    非 200 拋 GenerationError（帶 status code）；連線 / JSON 解析錯誤原樣往上拋，
    交給呼叫端統一 except (httpx.HTTPError, json.JSONDecodeError, ValueError, GenerationError)。
    """
    settings = get_settings()
    base_url, token, _ = _resolve_llm_creds(settings)
    headers = {
        "Authorization": f"Bearer {token}",
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    timeout = httpx.Timeout(
        connect=settings.http_connect_timeout,
        read=read_timeout,
        write=read_timeout,
        pool=settings.http_connect_timeout,
    )
    url = f"{base_url.rstrip('/')}/v1/messages"
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, json=payload, headers=headers)
    if resp.status_code != 200:
        raise GenerationError(f"MiniMax API 非 200: status={resp.status_code}")
    body = resp.json()
    text = "".join(
        blk.get("text", "") for blk in body.get("content", []) if blk.get("type") == "text"
    )
    return {"text": text}


def _parse_fenced_json(text: str, open_ch: str, close_ch: str) -> Any | None:
    """剝 code fence → json.loads；失敗退路抓第一個 open_ch...close_ch 區段再試一次。"""
    s = text.strip()
    if s.startswith("```"):
        # ```json ... ``` 或 ``` ... ```
        s = s.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        start, end = s.find(open_ch), s.rfind(close_ch)
        if start < 0 or end <= start:
            return None
        try:
            return json.loads(s[start : end + 1])
        except json.JSONDecodeError:
            return None


async def translate_word(word: str) -> dict[str, Any] | None:
    """翻一個英文單字到台繁中文。

    回傳 {translation, ipa?, pos?}（任一欄位缺失仍回 dict，給 caller 決定容錯）。
    LLM 失敗 / timeout / 解析爛掉 → 回 None（caller 寫 log、不擋主流程）。
    """
    settings = get_settings()
    rules = _numbered_rules([*_TRANSLATION_RULES, "輸出嚴格 JSON，不要解釋、不要 code fence。"])
    prompt = (
        "你是英文單字翻譯助手。對給定的英文單字輸出 JSON 物件：\n"
        '{"translation": "<繁體中文（台灣用語）>", '
        '"ipa": "<IPA 音標；若不確定省略>", '
        '"pos": ["<詞性，例如 n/v/adj>"], '
        '"example_en": "<一個用到這個單字的英文例句>", '
        '"example_zh": "<上述例句的繁體中文翻譯>", '
        '"mnemonic": "<諧音/關鍵字記憶提示>"}\n'
        f"規則：\n{rules}\n"
        f"單字：{word}"
    )
    _, _, model = _resolve_llm_creds(settings)
    payload = {
        "model": model,
        # MiniMax-M2.7 是推理模型，回答前會先吐一段 thinking block（實測 ~1800 tokens）；
        # 1024 太小會讓 thinking 吃光預算、答案永遠生不出來，導致 100% 回 None。
        "max_tokens": 4096,
        "messages": [{"role": "user", "content": prompt}],
    }
    try:
        result = await _call_minimax(payload, settings.http_read_timeout)
        return _parse_text(result["text"], word)
    except (httpx.HTTPError, json.JSONDecodeError, ValueError, GenerationError) as exc:
        logger.warning("MiniMax 翻譯失敗 word=%s: %s", word, exc)
        return None


def _parse_text(text: str, word: str | None = None) -> dict[str, Any] | None:
    """剝 code fence → JSON parse → 回 dict（含健壯性退路）。"""
    obj = _parse_fenced_json(text, "{", "}")
    if not isinstance(obj, dict):
        return None
    return _normalize_payload(obj, word)


def _normalize_payload(obj: Any, word: str | None = None) -> dict[str, Any] | None:
    """把 LLM 回的單筆 JSON 物件整理成 worker 用的 payload（任一欄位缺仍回 dict）。"""
    if not isinstance(obj, dict):
        return None
    out: dict[str, Any] = {}
    tr = obj.get("translation")
    if isinstance(tr, str) and tr.strip():
        out["translation"] = tr.strip()
    ipa = obj.get("ipa")
    if isinstance(ipa, str) and ipa.strip():
        out["ipa"] = ipa.strip()
    pos = obj.get("pos")
    if isinstance(pos, list):
        out["pos"] = [str(p).strip() for p in pos if str(p).strip()]
    elif isinstance(pos, str) and pos.strip():
        out["pos"] = [pos.strip()]
    ex_en = obj.get("example_en")
    if isinstance(ex_en, str) and ex_en.strip():
        out["example_en"] = ex_en.strip()
    ex_zh = obj.get("example_zh")
    if isinstance(ex_zh, str) and ex_zh.strip():
        out["example_zh"] = ex_zh.strip()
    # 走跟 refine 同一道門檻：查不到的字即時翻譯時，也別產出「發音像…」那種罐頭。
    mnemonic = _clean_str(obj.get("mnemonic"))
    if mnemonic and (word is None or mnemonic_ok(word, mnemonic)):
        out["mnemonic"] = mnemonic
    return out or None


async def translate_batch(words: list[str]) -> dict[str, dict[str, Any] | None]:
    """一次翻 N 個英文單字，回傳 {word: payload}。整批 API 失敗回空 dict。

    payload 形狀同 translate_word；某字 LLM 沒翻出來（缺欄位）對應 None，
    給 caller 決定是否走單字重試或忽略。
    """
    if not words:
        return {}
    settings = get_settings()
    word_list = "\n".join(words)
    rules = _numbered_rules(
        [
            "word 欄位必須 echo 對應的英文單字（小寫、不可變）。",
            *_TRANSLATION_RULES,
            "嚴格只輸出 JSON 陣列，不要解釋、不要 code fence。",
        ]
    )
    prompt = (
        f"你是英文單字翻譯助手。對以下 {len(words)} 個英文單字，每個字各輸出一個 JSON 物件，"
        f"集合成 JSON 陣列回傳。每個物件必須包含 word 欄位（原樣 echo 輸入的英文單字）+：\n"
        '{"word": "<原樣英文單字>", '
        '"translation": "<繁體中文（台灣用語）>", '
        '"ipa": "<IPA 音標；若不確定省略>", '
        '"pos": ["<詞性，例如 n/v/adj>"], '
        '"example_en": "<一個用到這個單字的英文例句>", '
        '"example_zh": "<上述例句的繁體中文翻譯>", '
        '"mnemonic": "<諧音/關鍵字記憶提示>"}\n'
        f"規則：\n{rules}\n"
        f"單字列表（每行一個，順序固定）：\n{word_list}"
    )
    _, _, model = _resolve_llm_creds(settings)
    payload = {
        "model": model,
        "max_tokens": _BATCH_MAX_TOKENS,
        "messages": [{"role": "user", "content": prompt}],
    }
    try:
        result = await _call_minimax(payload, _BATCH_READ_TIMEOUT)
        text = result["text"]
        items = _parse_batch_text(text)
    except (httpx.HTTPError, json.JSONDecodeError, ValueError, GenerationError) as exc:
        logger.warning("MiniMax 批次翻譯例外 n=%d: %s: %s", len(words), type(exc).__name__, exc)
        return {}

    if items is None:
        logger.warning("MiniMax 批次翻譯解析失敗 n=%d text_head=%s", len(words), text[:200])
        return {}

    # 對齊 word → payload；LLM 可能漏字（順序亂掉也算漏）
    out: dict[str, dict[str, Any] | None] = {w: None for w in words}
    seen: set[str] = set()
    for item in items:
        word = item.get("word")
        if not isinstance(word, str) or word not in out or word in seen:
            continue
        seen.add(word)
        payload_obj = _normalize_payload(item, word)
        if payload_obj and payload_obj.get("translation"):
            out[word] = payload_obj
    return out


def _parse_batch_text(text: str) -> list[dict[str, Any]] | None:
    """剝 code fence → JSON parse → 回 list[dict]（容錯退路：抓 [...] 區段）。"""
    obj = _parse_fenced_json(text, "[", "]")
    if isinstance(obj, list):
        return [x for x in obj if isinstance(x, dict)]
    return _salvage_objects(text) or None


def _salvage_objects(text: str) -> list[dict[str, Any]]:
    """從截斷的 JSON 陣列裡撈出已經寫完的物件。

    輸出撞到 max_tokens 就會停在半個陣列，整批 25 個字一起作廢——freq 那批最後
    連三次踩到這個，被 3-strike 保護中止。前面十幾個物件其實是完整的，救回來，
    沒救到的字維持 quality = 0，下一輪自然會再撈。
    """
    out: list[dict[str, Any]] = []
    depth, start, in_str, esc = 0, -1, False, False
    for i, ch in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth:
            depth -= 1
            if depth == 0:
                try:
                    obj = json.loads(text[start : i + 1])
                except json.JSONDecodeError:
                    continue
                if isinstance(obj, dict):
                    out.append(obj)
    return out


# --- 義項精煉（migration 0030）-------------------------------------------------
# translate_* 是「這個字不在 dict_cache，生一筆」；refine_batch 是「這個字有一筆
# ECDICT dump，重寫成義項」。兩者輸入不同（後者要把原始 translation 餵進去當素材）、
# 輸出形狀不同，共用 _call_minimax / _parse_batch_text 就好，不硬併成一支。

_MAX_SENSES = 4
# 25 字實測會偶發截斷（輸出停在半個 JSON 陣列、解析回 None，整批白跑）。
# 巢狀 senses 讓輸出比 translate_batch 大不少，對齊 chat.py 的 16384。
_REFINE_MAX_TOKENS = 16384
_REFINE_READ_TIMEOUT = 240.0  # 輸出比 translate_batch 大（巢狀 senses），再放寬

# 三個 pass 的溫度刻意不同：義項/例句要每批寫法一致所以壓低；諧音聯想要每個字
# 都不一樣所以放高（0030 版共用一次呼叫，低溫把諧音塌成 1387 筆同一句型）；
# 複審是判斷題，不需要創意。
_REFINE_TEMPERATURE = 0.2
_MNEMONIC_TEMPERATURE = 0.9
_REVIEW_TEMPERATURE = 0.1

# 確定性防線：prompt 是機率性的，這幾條長度/關鍵詞檢查不是。
_MAX_ZH_LEN = 6
_MAX_CORE_LEN = 12
_MAX_MNEMONIC_LEN = 30
_MIN_MNEMONIC_WORD_LEN = 6
# LLM 傾向把詞源解釋寫進 core_sense（box →「…從盒子延伸為打拳的方場」）。
# 這些串接詞是可靠訊號：出現就代表它在「解釋」而不是在給一個意象。
_CORE_BAD_MARKERS = ("延伸為", "延伸出", "亦指", "亦可指", "引申")

_REFINE_RULES: tuple[str, ...] = (
    "word 欄位必須 echo 對應的英文單字（小寫、不可變）。",
    f"senses 依實際使用頻率排序，最多 {_MAX_SENSES} 筆；罕見、專業領域、"
    "古語的義項直接丟掉，不要為了完整而保留。",
    f"senses[].zh 是精簡對應詞，≤{_MAX_ZH_LEN} 個中文字，最多用一個頓號分隔兩個近義詞；"
    "不要堆逗號、不要寫英英定義、不要放同義詞列表。",
    "senses[].pos 用 n./v./vt./vi./adj./adv./prep./conj. 這類縮寫。",
    f"core_sense 是「意象」不是「定義」：≤{_MAX_CORE_LEN} 字，描述貫穿各義項的共同畫面，"
    "讓學習者能從一個概念推出其他義項。不得出現「延伸為」「亦指」「引申」這類串接說明語"
    "——那是在解釋詞源，不是給意象。",
    "core_sense 下列兩種情況一律填 null，不要為了填滿欄位硬湊："
    "(a) senses 只有 1 筆；"
    "(b) 同形異義字，各義項本來就沒有共同意象（bank 銀行/河岸、bat 蝙蝠/球棒、box 盒子/拳擊）。",
    "example_en 必須是完整句子（有主詞有動詞、句點結尾、≤15 字），"
    "而且要展示 senses[0] 那個義項的典型用法——不是其他義項、不是片語殘句。",
    "example_zh 為 example_en 的台繁翻譯。",
    "所有中文都必須是繁體中文台灣用詞。",
    "嚴格只輸出 JSON 陣列，不要解釋、不要 code fence。",
)

# few-shot：三個範例挑的是 core_sense 的三種情況（單義留白、同形異義留白、真的該填）。
# 純文字規則管不住「留白是正常的」——0030 版只用文字說「想不出填 null」，
# 結果多義字 core_sense 覆蓋率 97%，等於它幾乎從不留白。範例裡出現 null 才有效。
_REFINE_SHOTS = """範例輸入：
ambulance\t救護車, 傷病運送車
bank\tn. 銀行\\n堤, 岸, 沙洲\\nvt. 存款, 築堤防
run\tvi. 奔跑\\n(機器)運轉\\n(顏色)蔓延\\n競選\\nvt. 經營, 管理\\n刺穿

範例輸出：
[{"word":"ambulance","senses":[{"pos":"n.","zh":"救護車"}],"core_sense":null,\
"example_en":"The ambulance arrived in five minutes.","example_zh":"救護車五分鐘內就到了。"},\
{"word":"bank","senses":[{"pos":"n.","zh":"銀行"},{"pos":"n.","zh":"河岸"},{"pos":"vt.","zh":"存入"}],\
"core_sense":null,"example_en":"She went to the bank to deposit money.",\
"example_zh":"她去銀行存錢。"},\
{"word":"run","senses":[{"pos":"vi.","zh":"奔跑"},{"pos":"vi.","zh":"運轉"},{"pos":"vt.","zh":"經營"},\
{"pos":"vi.","zh":"蔓延"}],"core_sense":"持續的流動或運作",\
"example_en":"I run every morning before work.","example_zh":"我每天上班前跑步。"}]

（ambulance 只有一個義項所以 core_sense 留 null；bank 的「銀行」和「河岸」在英語裡
本來就是不同字源，硬找共同意象只會誤導，也留 null；run 的各義項確實共用一個畫面才填。）"""


async def refine_batch(entries: list[dict[str, str]]) -> dict[str, dict[str, Any] | None]:
    """把 ECDICT 原始 dump 精煉成結構化義項，回傳 {word: payload}。

    entries 每筆 {"word": str, "translation": str}——原始 translation 當素材餵進去，
    LLM 只做篩選重寫，不用從零產生義項（也避免它自由發揮出字典裡沒有的意思）。

    payload = {senses, core_sense?, example_en?, example_zh?}。諧音不在這裡產出，
    它有自己的 pass（mnemonic_batch）——見該函式的說明。
    整批 API 失敗回空 dict；個別字沒精煉出 senses 對應 None，caller 自行決定跳過或重試。
    """
    if not entries:
        return {}
    settings = get_settings()
    words = [e["word"] for e in entries]
    material = "\n".join(f"{e['word']}\t{e['translation']}" for e in entries)
    rules = _numbered_rules(list(_REFINE_RULES))
    prompt = (
        f"你在整理一份給台灣英語學習者的單字卡資料庫。以下 {len(entries)} 個英文單字，"
        "每個附了一份粗糙的字典原始釋義（欄位以 tab 分隔）。請為每個字輸出一個 JSON 物件，"
        "集合成 JSON 陣列回傳：\n"
        '{"word": "<原樣英文單字>", '
        '"senses": [{"pos": "<詞性縮寫>", "zh": "<精簡中文對應詞>"}], '
        '"core_sense": "<核心語意，不適用時填 null>", '
        '"example_en": "<展示 senses[0] 的完整英文例句>", '
        '"example_zh": "<上述例句的台繁翻譯>"}\n'
        f"規則：\n{rules}\n\n{_REFINE_SHOTS}\n\n"
        f"單字與原始釋義（每行一個）：\n{material}"
    )
    _, _, model = _resolve_llm_creds(settings)
    payload = {
        "model": model,
        "max_tokens": _REFINE_MAX_TOKENS,
        "temperature": _REFINE_TEMPERATURE,
        "messages": [{"role": "user", "content": prompt}],
    }
    items = await _call_batch_json(payload, len(entries), "義項精煉")
    if items is None:
        return {}

    out: dict[str, dict[str, Any] | None] = {w: None for w in words}
    seen: set[str] = set()
    for item in items:
        word = item.get("word")
        if not isinstance(word, str) or word not in out or word in seen:
            continue
        seen.add(word)
        out[word] = _normalize_refined(item)
    return out


def _clean_str(val: Any) -> str | None:
    """LLM 常把 null 寫成字串 "null"/"None"/""，一律當沒填。"""
    if not isinstance(val, str):
        return None
    s = val.strip()
    return s if s and s.lower() not in {"null", "none", "n/a"} else None


def _normalize_refined(obj: dict[str, Any]) -> dict[str, Any] | None:
    """整理精煉結果；senses 生不出來就整筆作廢（其他欄位單獨存在沒有意義）。"""
    raw_senses = obj.get("senses")
    if not isinstance(raw_senses, list):
        return None
    senses: list[dict[str, str]] = []
    for s in raw_senses[:_MAX_SENSES]:
        if not isinstance(s, dict):
            continue
        zh = _clean_str(s.get("zh"))
        if not zh:
            continue
        pos = _clean_str(s.get("pos"))
        senses.append({"pos": pos, "zh": zh} if pos else {"zh": zh})
    if not senses:
        return None

    out: dict[str, Any] = {"senses": senses}
    # 單義字不該有 core_sense：它的價值是串起多個義項，只有一個義項時是純雜訊。
    core = _clean_str(obj.get("core_sense"))
    if core and len(senses) > 1 and core_sense_ok(core):
        out["core_sense"] = core
    for key in ("example_en", "example_zh"):
        val = _clean_str(obj.get(key))
        if val:
            out[key] = val
    # 例句要成對才有用：只有英文沒中文（或反過來）就兩個都丟。
    if bool(out.get("example_en")) != bool(out.get("example_zh")):
        out.pop("example_en", None)
        out.pop("example_zh", None)
    return out


def core_sense_ok(core: str) -> bool:
    """core_sense 是否過關。太長或帶詞源串接語就退掉——寧可留白也不要一句解釋。"""
    return len(core) <= _MAX_CORE_LEN and not any(m in core for m in _CORE_BAD_MARKERS)


def mnemonic_ok(word: str, text: str) -> bool:
    """諧音提示是否值得留。

    短字（<6 字母）本來就不需要諧音拐杖，而且它們正是 0030 版產出最多廢話的地方
    （the / of / and 都被生了一條）。「發音像」開頭是模板塌陷的指紋，一律退。
    詞根拆解會帶英文（transport →「trans(橫越)+port(港口)」），所以不能一律禁英文；
    但出現的英文必須真的是這個字的一段，否則就是模型在講別的字。

    ponytail: 只擋得住這幾種確定性的爛法。「墾腿瑞」那種硬拼音節堆沒有可靠的
    程式判準（要有中文詞庫才測得出來），交給 prompt 的判準擋。
    """
    return (
        len(word) >= _MIN_MNEMONIC_WORD_LEN
        and len(text) <= _MAX_MNEMONIC_LEN
        and not text.startswith("發音像")
        and all(run in word.lower() for run in re.findall(r"[A-Za-z]+", text.lower()))
    )


# --- 諧音提示（獨立 pass）-----------------------------------------------------
# 跟 refine_batch 分開跑不是為了整齊，是因為兩者要的溫度相反：義項/例句要收斂，
# 諧音要發散。0030 版共用一次呼叫（低溫），1869 筆諧音裡 1387 筆是同一個開頭。

_MNEMONIC_RULES: tuple[str, ...] = (
    "依序試三種寫法，前面成立就不要用後面的：",
    "(甲) 詞根拆解——字首/字根/字尾拆得開，而且拆出來的意思真的串得回字義。"
    "這是首選，因為同一組詞根之後還會在別的字重複出現。"
    "不確定真正的詞源就跳到 (乙)，絕對不要編一個看起來像詞源的東西。",
    "(乙) 諧音——拆不開時才用，而且諧音的中文必須本身就是一句通順的話"
    "（「俺不能死」可以）。把音節硬拼成沒人這樣講的三字組不算通順，跳到 (丙)。",
    "(丙) null——甲乙都不成立就填 null。null 是正常答案，不是失敗，"
    "整批有一半以上是 null 很合理。寧可留白也不要塞一條看不懂的。",
    "提示要說出該字的意思，不能只是把中文釋義原樣搬回來當解釋。",
    "功能詞、代名詞、介系詞、連接詞、通行音譯詞（pizza、coffee）一律 null。",
    "不要套固定句型，每個字自己想。",
    f"≤{_MAX_MNEMONIC_LEN} 字，全繁體中文台灣用詞，除了要拆的字根本身不要夾英文。",
    "不雅、涉及髒話或人身冒犯的聯想一律 null。",
    "嚴格只輸出 JSON 陣列，不要解釋、不要 code fence。",
)

# 範例字刻意挑不會出現在高頻批次裡的：上一版拿 country/company 當反例，模型直接
# 把它們當查表用（那四個字回 null，其餘 16 個照樣硬湊），所以這裡只示範判準。
_MNEMONIC_SHOTS = """範例：
transport（運輸）  → "trans(橫越)+port(港口) → 運過海港"   甲：詞根真的串得回字義
predict（預測）    → "pre(事先)+dict(說) → 事先說出來"     甲
ambulance（救護車）→ "俺不能死 → 快叫救護車"               乙：拆不開，但諧音是通順的話
salmon（鮭魚）     → null   拆不開，「薩蒙」也不是話——寧可留白
liberal（自由的）  → null   liber(自由) 拆得開，但串不回「開明的」這個常用義
tomato（番茄）     → null   通行音譯，換個寫法幫不上記憶
under（在下面）    → null   介系詞，沒有值得記的畫面"""


async def mnemonic_batch(entries: list[dict[str, str]]) -> dict[str, str | None]:
    """為一批字生諧音提示。entries 每筆 {"word", "zh"}（zh 給聯想一個語意錨點）。

    回傳只含 LLM 有回答的字：{word: 提示 or None}。整批失敗回空 dict，
    caller 據此判斷該重試還是把該字標成「跑過了但不需要提示」。
    """
    if not entries:
        return {}
    settings = get_settings()
    words = {e["word"] for e in entries}
    material = "\n".join(f"{e['word']}\t{e['zh']}" for e in entries)
    prompt = (
        f"你在為台灣英語學習者的單字卡挑選記憶提示。以下 {len(entries)} 個字"
        "（tab 後是它的中文意思）。請輸出 JSON 陣列，每個字一個物件：\n"
        '{"word": "<原樣英文單字>", "mnemonic": "<提示，不適用填 null>"}\n'
        f"規則：\n{_numbered_rules(list(_MNEMONIC_RULES))}\n\n{_MNEMONIC_SHOTS}\n\n"
        f"單字（每行一個）：\n{material}"
    )
    _, _, model = _resolve_llm_creds(settings)
    payload = {
        "model": model,
        "max_tokens": _REFINE_MAX_TOKENS,
        "temperature": _MNEMONIC_TEMPERATURE,
        "messages": [{"role": "user", "content": prompt}],
    }
    items = await _call_batch_json(payload, len(entries), "諧音")
    if items is None:
        return {}

    out: dict[str, str | None] = {}
    for item in items:
        word = item.get("word")
        if not isinstance(word, str) or word not in words or word in out:
            continue
        text = _clean_str(item.get("mnemonic"))
        out[word] = text if text and mnemonic_ok(word, text) else None

    zh = {e["word"]: e["zh"] for e in entries}
    return await _judge_mnemonics(out, zh)


# 生成的模型會硬拆（through →「thr+ough」、country →「coun(鄉野)+try(地方)」都是編的），
# 但同一個模型當判官很準——core_sense 複審已經驗過這件事。假詞源比沒有提示更傷，
# 所以生完一律再審一遍，判不出來就當作沒有。
_JUDGE_RULES: tuple[str, ...] = (
    "詞根拆解：拆出來的成分必須是真的詞根或字綴。把字任意切兩半再各配一個意思"
    "（through →「thr+ough」、country →「coun(鄉野)+try(地方)」）是編的，drop。",
    "諧音：那串中文本身要是一句通順的話。硬拼的音節堆 drop。",
    "提示必須說出字義。把中文釋義原樣搬回來、或句子裡直接出現該英文單字當解釋的，drop。",
    "不雅、涉及髒話或人身冒犯的，drop。",
    "拿不準是不是真詞源就 drop——寧可沒有提示，也不要教錯的詞根。",
    # 別在這裡加「不要誤殺正確詞根」之類的平衡句：實測加了之後判官整個放水，
    # through →「th 粗氣過＋rough 粗獷」這種硬拆全部回來（16/20 通過）。
    # 嚴格版誤殺 company（com+pan 麵包）這類真詞源，但通過的 100% 正確，划算。
    "嚴格只輸出 JSON 陣列，不要解釋、不要 code fence。",
)


async def _judge_mnemonics(
    cand: dict[str, str | None], zh: dict[str, str]
) -> dict[str, str | None]:
    """審一批候選提示，沒過的降成 None。整批審不動就原樣退回（下輪還會再撈）。"""
    filled = {w: t for w, t in cand.items() if t}
    if not filled:
        return cand
    settings = get_settings()
    material = "\n".join(f"{w}\t{zh.get(w, '')}\t{t}" for w, t in filled.items())
    prompt = (
        f"你在審查台灣英語學習者單字卡上的記憶提示。以下 {len(filled)} 筆"
        "（tab 分隔：英文單字、中文意思、待審提示）。請輸出 JSON 陣列：\n"
        '{"word": "<原樣英文單字>", "verdict": "keep" 或 "drop"}\n'
        f"判準：\n{_numbered_rules(list(_JUDGE_RULES))}\n\n"
        f"待審（每行一筆）：\n{material}"
    )
    _, _, model = _resolve_llm_creds(settings)
    items = await _call_batch_json(
        {
            "model": model,
            "max_tokens": _REFINE_MAX_TOKENS,
            "temperature": _REVIEW_TEMPERATURE,
            "messages": [{"role": "user", "content": prompt}],
        },
        len(filled),
        "提示複審",
    )
    if items is None:
        return cand

    out = dict(cand)
    for item in items:
        word = item.get("word")
        if isinstance(word, str) and word in filled and item.get("verdict") == "drop":
            out[word] = None
    return out


# --- 核心語意複審（獨立 pass）-------------------------------------------------
# 長度/關鍵詞檢查抓不到「硬湊」：box 的「四面封閉的空間」語法完全合規，問題是
# 拳擊的 box 跟盒子在英語裡本來就不同源。這種只能讓 model 拿著規則回頭審一遍。

_REVIEW_RULES: tuple[str, ...] = (
    "逐字判斷現有的 core_sense 是否真的成立。verdict 只能是 keep / rewrite / drop。",
    "drop 的情況：各義項其實沒有共同意象（同形異義、或詞義早已分家），"
    "現有描述是硬湊出來的；或它只是在複述 senses[0]，對理解其他義項沒有幫助。",
    f"rewrite 的情況：意象方向對但寫成了解釋、超過 {_MAX_CORE_LEN} 字、"
    "或用了「延伸為」「亦指」「引申」這類串接說明語。此時在 core_sense 欄給新版本。",
    "keep 的情況：現有描述已經是一個能推出各義項的具體意象。core_sense 原樣回傳。",
    "判準是「學習者看了這句，能不能自己推出第 2、3 個義項」。不能就是 drop。"
    "留白沒有成本，硬湊有——寧可 drop。",
    "嚴格只輸出 JSON 陣列，不要解釋、不要 code fence。",
)

_REVIEW_SHOTS = """範例：
box    盒子/拳擊    「四面封閉的空間，從盒子延伸為打拳的方場」
       → drop：拳擊的 box 與容器的 box 不同源，這句是編出來的
run    奔跑/運轉/經營  「持續的流動或運作」
       → keep：從這個畫面確實推得出「機器運轉」「經營公司」
issue  發行/議題/流出  「從內部往外流出而產生」
       → keep
cool   涼的/冷靜的   「溫度低或情緒冷，帶有從熱退到冷的意象」
       → rewrite：方向對但寫成解釋，改成「熱度退去的狀態」"""


async def review_core_senses(
    entries: list[dict[str, Any]],
) -> dict[str, str | None]:
    """複審一批 core_sense。entries 每筆 {"word", "senses", "core_sense"}。

    回傳只含 LLM 有回答的字：{word: 保留/改寫後的值 or None（審掉）}。
    整批失敗回空 dict。
    """
    if not entries:
        return {}
    settings = get_settings()
    words = {e["word"] for e in entries}
    material = "\n".join(
        "{}\t{}\t{}".format(
            e["word"],
            " / ".join(s.get("zh", "") for s in e["senses"]),
            e["core_sense"],
        )
        for e in entries
    )
    prompt = (
        f"你在複審一份給台灣英語學習者的單字卡資料。以下 {len(entries)} 個多義字，"
        "每行是「單字 / 各義項 / 現有的核心語意」（tab 分隔）。請輸出 JSON 陣列：\n"
        '{"word": "<原樣英文單字>", "verdict": "keep|rewrite|drop", '
        '"core_sense": "<keep 原樣回傳、rewrite 給新版、drop 填 null>"}\n'
        f"規則：\n{_numbered_rules(list(_REVIEW_RULES))}\n\n{_REVIEW_SHOTS}\n\n"
        f"待複審（每行一個）：\n{material}"
    )
    _, _, model = _resolve_llm_creds(settings)
    payload = {
        "model": model,
        "max_tokens": _REFINE_MAX_TOKENS,
        "temperature": _REVIEW_TEMPERATURE,
        "messages": [{"role": "user", "content": prompt}],
    }
    items = await _call_batch_json(payload, len(entries), "核心語意複審")
    if items is None:
        return {}

    out: dict[str, str | None] = {}
    for item in items:
        word = item.get("word")
        if not isinstance(word, str) or word not in words or word in out:
            continue
        core = _clean_str(item.get("core_sense"))
        # drop 之外的 verdict 也要過同一道長度/關鍵詞檢查——複審自己也會寫太長。
        keep = item.get("verdict") != "drop" and core is not None and core_sense_ok(core)
        out[word] = core if keep else None
    return out


async def _call_batch_json(
    payload: dict[str, Any], n: int, label: str
) -> list[dict[str, Any]] | None:
    """打一次 MiniMax 並解析成 list[dict]；失敗回 None（已記錄 log）。"""
    try:
        text = (await _call_minimax(payload, _REFINE_READ_TIMEOUT))["text"]
        items = _parse_batch_text(text)
    except (httpx.HTTPError, json.JSONDecodeError, ValueError, GenerationError) as exc:
        logger.warning("MiniMax %s 例外 n=%d: %s: %s", label, n, type(exc).__name__, exc)
        return None
    if items is None:
        logger.warning("MiniMax %s 解析失敗 n=%d text_head=%s", label, n, text[:200])
    return items
