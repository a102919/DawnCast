"""寫稿回應解析（LangGraph pod 的 write_script / judge 共用）。

prompt 組裝在 langgraph_pod/nodes.py（_build_outline_messages / _build_segment_messages）；
這裡只負責剝 code fence 並驗證成合法 ScriptJSON。任何解析 / 驗證失敗一律 raise GenerationError，
觸發語意層重試（RetryPolicy 控制硬上限，PRD §6 防重生風暴）。
"""

from __future__ import annotations


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
