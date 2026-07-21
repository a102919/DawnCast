"""寫稿回應解析（LangGraph pod 的 write_script / judge 共用）。

prompt 組裝在 langgraph_pod/nodes.py（_build_pod_messages）；這裡只負責剝 code fence
並驗證成合法 ScriptJSON。任何解析 / 驗證失敗一律 raise GenerationError，
觸發語意層重試（RetryPolicy 控制硬上限，PRD §6 防重生風暴）。
"""

from __future__ import annotations

import json
import re

from pydantic import ValidationError as PydanticValidationError

from shared.errors import GenerationError
from shared.models import ScriptJSON, ScriptLine

from .base import EngineResult

# 單行過長時的強制切割上限（英文字數）。純安全網：prompt 端已引導 LLM 別寫太長，
# 這裡保底避免 LLM 沒聽話時逐字稿段落卡片太長。
_MAX_LINE_WORDS = 30

_EN_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")
_ZH_SENTENCE_RE = re.compile(r"(?<=[。！？!?])")


def _group_sentence_indices(sentences: list[str], max_words: int) -> list[tuple[int, int]]:
    """把句子貪婪分組，每組字數盡量壓在 max_words 內。回傳 [(start, end), ...]（半開區間）。"""
    groups: list[tuple[int, int]] = []
    start = 0
    words_in_group = 0
    for i, sentence in enumerate(sentences):
        w = len(sentence.split())
        if i > start and words_in_group + w > max_words:
            groups.append((start, i))
            start = i
            words_in_group = 0
        words_in_group += w
    groups.append((start, len(sentences)))
    return groups


def _split_long_lines(
    lines: list[ScriptLine], max_words: int = _MAX_LINE_WORDS
) -> list[ScriptLine]:
    """逐字稿段落太長時的保底切割：每行英文字數超過 max_words 才切，否則原樣保留。

    zh 翻譯用中文句尾標點各自切句，句數跟英文一致時 1:1 對應分組；句數不一致時
    ponytail: 按比例映射分組邊界（近似值，非精準句對齊，翻譯本來就不保證逐句對應）。
    pause_before 只留在第一組（chapter 邊界語意不變），其餘組別用預設短停頓即可。
    """
    out: list[ScriptLine] = []
    for line in lines:
        if len(line.text.split()) <= max_words:
            out.append(line)
            continue

        en_sentences = [s for s in _EN_SENTENCE_RE.split(line.text.strip()) if s]
        if len(en_sentences) <= 1:
            out.append(line)  # 單句本身就過長，沒有標點可切，維持原樣（已知上限）
            continue

        zh_sentences = [s for s in _ZH_SENTENCE_RE.split(line.zh.strip()) if s]
        n_en, n_zh = len(en_sentences), len(zh_sentences)
        groups = _group_sentence_indices(en_sentences, max_words)

        for group_idx, (start, end) in enumerate(groups):
            text_chunk = " ".join(en_sentences[start:end])
            if n_zh == n_en:
                zh_chunk = "".join(zh_sentences[start:end])
            else:
                zh_start = round(start * n_zh / n_en) if n_en else 0
                zh_end = round(end * n_zh / n_en) if n_en else n_zh
                zh_chunk = "".join(zh_sentences[zh_start:zh_end]) or line.zh
            out.append(
                ScriptLine(
                    speaker=line.speaker,
                    text=text_chunk,
                    zh=zh_chunk,
                    pause_before=line.pause_before if group_idx == 0 else False,
                )
            )
    return out


def _strip_code_fence(raw_text: str) -> str:
    """剝掉可能包住 JSON 的 ```json ... ``` code fence。"""
    text = raw_text.strip()
    if not text.startswith("```"):
        return text
    # 去掉開頭 fence 行（```json 或 ```）
    lines = text.split("\n")
    lines = lines[1:]
    # 去掉結尾 fence 行
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def parse_engine_result(
    raw_text: str,
    *,
    engine: str,
    model: str,
    usage: dict[str, object],
) -> EngineResult:
    """剝 code fence → 驗證成 ScriptJSON → 包成 EngineResult。

    解析或契約驗證失敗一律 raise GenerationError（不洩漏完整原文進對外訊息）。
    """
    cleaned = _strip_code_fence(raw_text)
    try:
        script = ScriptJSON.model_validate_json(cleaned)
    except (PydanticValidationError, json.JSONDecodeError) as exc:
        raise GenerationError(f"寫稿回應無法解析成合法 ScriptJSON：{exc}") from exc
    script = script.model_copy(update={"script": _split_long_lines(script.script)})
    return EngineResult(script=script, engine=engine, model=model, raw_usage=usage)
