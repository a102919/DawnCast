"""寫稿回應解析（LangGraph pod 的 write_script / judge 共用）。

prompt 組裝在 langgraph_pod/nodes.py（_build_pod_messages）；這裡只負責剝 code fence
並驗證成合法 ScriptJSON。任何解析 / 驗證失敗一律 raise GenerationError，
觸發語意層重試（RetryPolicy 控制硬上限，PRD §6 防重生風暴）。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from itertools import accumulate

from pydantic import ValidationError as PydanticValidationError

from shared.errors import GenerationError
from shared.models import ScriptJSON, ScriptLine


@dataclass(frozen=True)
class EngineResult:
    """寫稿結果。raw_usage 留原始 token 統計給觀測 / 成本核算。

    舊的三引擎 adapter（minimax / api_key / claude_code）已退役——production 路徑
    統一走 langgraph_pod/chat.py 的 ChatModel，這裡只剩解析端用的結果 DTO，不放任何 I/O。
    """

    script: ScriptJSON
    engine: str
    model: str
    raw_usage: dict[str, object] = field(default_factory=dict)

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


def _zh_split_points(
    groups: list[tuple[int, int]], en_sentences: list[str], zh_sentences: list[str]
) -> list[int]:
    """對每個 en 分組邊界，選累積長度比例最接近的 zh 句邊界（單調遞增）。

    en 進度用字數、zh 進度用字元數衡量；比句數比例映射準——翻譯常把兩個英文短句
    併成一句中文（或反過來），句數比例會把邊界推錯一句。
    """
    en_cum = list(accumulate(len(s.split()) for s in en_sentences))
    zh_cum = list(accumulate(len(s) for s in zh_sentences))
    total_en = en_cum[-1] or 1
    total_zh = zh_cum[-1] or 1
    points: list[int] = []
    prev = 0
    for _, end in groups[:-1]:
        frac = en_cum[end - 1] / total_en
        best = min(
            range(prev, len(zh_sentences) + 1),
            key=lambda i: abs((zh_cum[i - 1] if i else 0) / total_zh - frac),
        )
        points.append(best)
        prev = best
    return points


def _split_long_lines(
    lines: list[ScriptLine], max_words: int = _MAX_LINE_WORDS
) -> list[ScriptLine]:
    """逐字稿段落太長時的保底切割：每行英文字數超過 max_words 才切，否則原樣保留。

    zh 翻譯用中文句尾標點各自切句，句數跟英文一致時 1:1 對應分組；句數不一致時
    用累積長度比例找最接近的 zh 句邊界（_zh_split_points），避免中英字幕錯位一句。
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

        if n_zh == n_en:
            zh_bounds = [start for start, _ in groups] + [n_zh]
        else:
            zh_bounds = [0, *_zh_split_points(groups, en_sentences, zh_sentences), n_zh]

        for group_idx, (start, end) in enumerate(groups):
            text_chunk = " ".join(en_sentences[start:end])
            zh_chunk = "".join(zh_sentences[zh_bounds[group_idx] : zh_bounds[group_idx + 1]])
            out.append(
                ScriptLine(
                    speaker=line.speaker,
                    text=text_chunk,
                    zh=zh_chunk or line.zh,  # 邊界塌陷成空組時退回整句 zh（原有保底）
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
