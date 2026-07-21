"""寫稿結果的共用資料結構。

舊的三引擎 adapter（minimax / api_key / claude_code）已退役——production 路徑
統一走 langgraph_pod/chat.py 的 ChatModel。這裡只剩解析端（prompt.parse_engine_result）
用的結果 DTO，不放任何 I/O。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from shared.models import ScriptJSON


@dataclass(frozen=True)
class EngineResult:
    """寫稿結果。raw_usage 留原始 token 統計給觀測 / 成本核算。"""

    script: ScriptJSON
    engine: str
    model: str
    raw_usage: dict[str, object] = field(default_factory=dict)
