"""LLM 翻譯：輕量級 util，不污染 engine Protocol。

dict_translate worker 與 /dict/lookup fallback 共用。
直接走 MiniMax Anthropic 相容 endpoint（與 podcast 生成同一服務、同一帳號）。
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from shared.config import Settings, get_settings
from shared.errors import GenerationError

logger = logging.getLogger(__name__)

_BATCH_MAX_TOKENS = 8192  # 10 字冷僻字實測 6808 tokens（thinking + output），留 buffer
_BATCH_READ_TIMEOUT = 180.0  # 10 字冷僻字實測 80s，留 2x buffer；覆寫 settings.http_read_timeout


def _resolve_llm_creds(settings: Settings) -> tuple[str, str, str]:
    """依 generation_engine 選 (base_url, auth_token, model)，跟 chat.make_langchain_chat 對齊。

    之前這裡永遠讀 minimax_auth_token，production 用 GENERATION_ENGINE=api_key
    時該欄位是空的，導致 dict_translate 100% 401。
    """
    if settings.generation_engine == "api_key":
        return settings.api_base_url, settings.api_key, settings.api_model
    return settings.minimax_anthropic_base_url, settings.minimax_auth_token, settings.minimax_model


async def translate_word(word: str) -> dict[str, Any] | None:
    """翻一個英文單字到台繁中文。

    回傳 {translation, ipa?, pos?}（任一欄位缺失仍回 dict，給 caller 決定容錯）。
    LLM 失敗 / timeout / 解析爛掉 → 回 None（caller 寫 log、不擋主流程）。
    """
    settings = get_settings()
    prompt = (
        "你是英文單字翻譯助手。對給定的英文單字輸出 JSON 物件：\n"
        '{"translation": "<繁體中文（台灣用語）>", '
        '"ipa": "<IPA 音標；若不確定省略>", '
        '"pos": ["<詞性，例如 n/v/adj>"], '
        '"example_en": "<一個用到這個單字的英文例句>", '
        '"example_zh": "<上述例句的繁體中文翻譯>"}\n'
        "規則：\n"
        "1. translation 必須繁體中文台灣用詞（網路/網路、磁碟/磁碟、滑鼠/滑鼠）。\n"
        "2. example_en 要自然、簡短（≤ 15 字），能展示該字典型用法。\n"
        "3. example_zh 為 example_en 的逐字台繁翻譯。\n"
        "4. 輸出嚴格 JSON，不要解釋、不要 code fence。\n"
        f"單字：{word}"
    )
    base_url, token, model = _resolve_llm_creds(settings)
    payload = {
        "model": model,
        # MiniMax-M2.7 是推理模型，回答前會先吐一段 thinking block（實測 ~1800 tokens）；
        # 1024 太小會讓 thinking 吃光預算、答案永遠生不出來，導致 100% 回 None。
        "max_tokens": 4096,
        "messages": [{"role": "user", "content": prompt}],
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    timeout = httpx.Timeout(
        connect=settings.http_connect_timeout,
        read=settings.http_read_timeout,
        write=settings.http_read_timeout,
        pool=settings.http_connect_timeout,
    )
    url = f"{base_url.rstrip('/')}/v1/messages"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, json=payload, headers=headers)
        if resp.status_code != 200:
            logger.warning("MiniMax 翻譯非 200 word=%s status=%d", word, resp.status_code)
            return None
        body = resp.json()
        text = "".join(
            blk.get("text", "") for blk in body.get("content", []) if blk.get("type") == "text"
        )
        return _parse_text(text)
    except (httpx.HTTPError, json.JSONDecodeError, ValueError, GenerationError) as exc:
        logger.warning("MiniMax 翻譯失敗 word=%s: %s", word, exc)
        return None


def _parse_text(text: str) -> dict[str, Any] | None:
    """剝 code fence → JSON parse → 回 dict（含健壯性退路）。"""
    s = text.strip()
    if s.startswith("```"):
        # ```json ... ``` 或 ``` ... ```
        s = s.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        obj = json.loads(s)
    except json.JSONDecodeError:
        # 退路：抓第一個 {...}
        start, end = s.find("{"), s.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            obj = json.loads(s[start : end + 1])
        except json.JSONDecodeError:
            return None
    if not isinstance(obj, dict):
        return None
    return _normalize_payload(obj)


def _normalize_payload(obj: Any) -> dict[str, Any] | None:
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
    prompt = (
        f"你是英文單字翻譯助手。對以下 {len(words)} 個英文單字，每個字各輸出一個 JSON 物件，"
        f"集合成 JSON 陣列回傳。每個物件必須包含 word 欄位（原樣 echo 輸入的英文單字）+：\n"
        '{"word": "<原樣英文單字>", '
        '"translation": "<繁體中文（台灣用語）>", '
        '"ipa": "<IPA 音標；若不確定省略>", '
        '"pos": ["<詞性，例如 n/v/adj>"], '
        '"example_en": "<一個用到這個單字的英文例句>", '
        '"example_zh": "<上述例句的繁體中文翻譯>"}\n'
        "規則：\n"
        "1. word 欄位必須 echo 對應的英文單字（小寫、不可變）。\n"
        "2. translation 必須繁體中文台灣用詞（網路/網路、磁碟/磁碟、滑鼠/滑鼠）。\n"
        "3. example_en 要自然、簡短（≤ 15 字），能展示該字典型用法。\n"
        "4. example_zh 為 example_en 的逐字台繁翻譯。\n"
        "5. 嚴格只輸出 JSON 陣列，不要解釋、不要 code fence。\n"
        f"單字列表（每行一個，順序固定）：\n{word_list}"
    )
    base_url, token, model = _resolve_llm_creds(settings)
    payload = {
        "model": model,
        "max_tokens": _BATCH_MAX_TOKENS,
        "messages": [{"role": "user", "content": prompt}],
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    timeout = httpx.Timeout(
        connect=settings.http_connect_timeout,
        read=_BATCH_READ_TIMEOUT,
        write=_BATCH_READ_TIMEOUT,
        pool=settings.http_connect_timeout,
    )
    url = f"{base_url.rstrip('/')}/v1/messages"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, json=payload, headers=headers)
    except Exception as exc:
        logger.warning("MiniMax 批次翻譯例外 n=%d: %s: %s", len(words), type(exc).__name__, exc)
        return {}

    if resp.status_code != 200:
        logger.warning("MiniMax 批次翻譯非 200 n=%d status=%d", len(words), resp.status_code)
        return {}

    body = resp.json()
    text = "".join(
        blk.get("text", "") for blk in body.get("content", []) if blk.get("type") == "text"
    )
    items = _parse_batch_text(text)
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
        payload_obj = _normalize_payload(item)
        if payload_obj and payload_obj.get("translation"):
            out[word] = payload_obj
    return out


def _parse_batch_text(text: str) -> list[dict[str, Any]] | None:
    """剝 code fence → JSON parse → 回 list[dict]（容錯退路：抓 [...] 區段）。"""
    s = text.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        obj = json.loads(s)
    except json.JSONDecodeError:
        start, end = s.find("["), s.rfind("]")
        if start < 0 or end <= start:
            return None
        try:
            obj = json.loads(s[start : end + 1])
        except json.JSONDecodeError:
            return None
    if isinstance(obj, list):
        return [x for x in obj if isinstance(x, dict)]
    return None
