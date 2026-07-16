"""寫稿 prompt 組裝與回應解析。三引擎共用這一份，確保輸出契約一致。

prompt 產 Anthropic Messages 格式（system + user）；解析端負責剝 code fence
並驗證成合法 ScriptJSON。任何解析 / 驗證失敗一律 raise GenerationError，
觸發語意層重試（呼叫端控制硬上限，PRD §6 防重生風暴）。
"""

from __future__ import annotations

import json
import re

from pydantic import ValidationError as PydanticValidationError

from shared.errors import GenerationError
from shared.models import ANGLES, ScriptJSON, ScriptLine

from .base import EngineResult, GenerationRequest

# 單行過長時的強制切割上限（英文字數）。純安全網：prompt 端已引導 LLM 別寫太長，
# 這裡保底避免 LLM 沒聽話時逐字稿段落卡片太長。
_MAX_LINE_WORDS = 30

_EN_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")
_ZH_SENTENCE_RE = re.compile(r"(?<=[。！？!?])")

# 角度說明對照表（從 shared.models 取，不讓 LLM 自己想角度）
ANGLE_DESC: dict[str, str] = dict(ANGLES)

# 寫稿輸出 schema 描述，直接寫進 prompt，逼 LLM 對齊 ScriptJSON 契約。
_SCHEMA_BLOCK = """You MUST output a single JSON object with EXACTLY these keys:
{
  "topic": string,                       // the episode topic, in English
  "extracted_facts": [                   // 3-5 key facts you based the script on
    {"claim": string, "source_ids": string[]}  // source_ids empty if no sources given
  ],
  "target_vocab": [                      // exactly the 3 preview words
    {"word": string, "explanation": string}  // explanation in simple English
  ],
  "script": [                            // the full two-host dialogue
    {"speaker": "Alex" | "Sarah", "text": string, "zh": string}
  ]
}"""


def _avoid_block(req: GenerationRequest) -> str:
    """V1.1 去重提示：要求避開既有 facts / 摘要。MVP 通常為空字串。"""
    if not req.avoid_facts and not req.avoid_summary:
        return ""
    lines = ["\n# AVOID REPETITION", "Do NOT repeat these points already covered before:"]
    lines.extend(f"- {fact}" for fact in req.avoid_facts)
    if req.avoid_summary:
        lines.append(f"Previous episode summary: {req.avoid_summary}")
    lines.append("Find a fresh take within the assigned angle.")
    return "\n".join(lines)


def build_messages(req: GenerationRequest) -> list[dict[str, str]]:
    """組 Anthropic Messages 格式（system + user）。"""
    lo, hi = req.target_minutes
    angle_desc = ANGLE_DESC.get(req.angle, "")

    system = f"""You are the head writer for "DawnCast", a daily English-learning podcast.
You write a natural, friendly conversation between TWO hosts: Alex and Sarah.

# AUDIENCE & LEVEL
- Target CEFR level: {req.cefr}. Use common everyday vocabulary and simple sentence
  structures. When a technical term is unavoidable, have a host explain it on the spot
  in plain, simple English.

# LENGTH
- Roughly {lo}-{hi} minutes of spoken audio: about 550-750 English words, 18-24 lines total.

# STRUCTURE
- Open with the hosts introducing themselves, then preview the 3 target vocabulary words.
- Build the body around the assigned angle.
- Close with a quick review of the 3 words and a warm sign-off.
- Keep each line to about 2-3 sentences. If an idea needs more, split it into a back-and-forth
  exchange (the other host asks, interrupts, or reacts) instead of one long monologue turn.

# ANGLE (the whole episode must revolve around this)
- {req.angle}（{angle_desc}）

# BILINGUAL REQUIREMENT
- EVERY script line MUST include a Traditional Chinese translation in the "zh" field.
- Use natural Taiwan Mandarin (台灣正體中文用詞), translate the meaning naturally —
  do NOT translate word-for-word stiffly.

# OUTPUT FORMAT
- Output ONLY the JSON object. No markdown, no code fences, no commentary.

{_SCHEMA_BLOCK}{_avoid_block(req)}"""

    user = f"""Write today's episode.
- Canonical topic: {req.canonical_topic}
- Big topic: {req.big_topic}
- Topic type: {req.topic_type}
- Angle: {req.angle}（{angle_desc}）

Return the JSON object now."""

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


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
