"""生成引擎 adapter 的共用契約（PRD §8）。

三實作（minimax / api_key / claude_code）同介面、同 prompt、同 ScriptJSON 契約，
env 一鍵切（可逆性是硬約束）。本檔只放資料結構與 Protocol，不放任何 I/O。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from shared.models import ScriptJSON


@dataclass(frozen=True)
class GenerationRequest:
    """寫稿請求。canonical_topic 是收斂後的具體題目，big_topic 是所屬大主題。"""

    canonical_topic: str
    big_topic: str
    topic_type: str
    angle: str = "定義"
    cefr: str = "B1"
    target_minutes: tuple[int, int] = (3, 4)
    avoid_facts: tuple[str, ...] = ()  # V1.1 去重用；MVP 通常空
    avoid_summary: str | None = None


@dataclass(frozen=True)
class EngineResult:
    """寫稿結果。raw_usage 留原始 token 統計給觀測 / 成本核算。"""

    script: ScriptJSON
    engine: str
    model: str
    raw_usage: dict[str, object] = field(default_factory=dict)


@runtime_checkable
class GenerationEngine(Protocol):
    """寫稿引擎介面。呼叫端只認這個 Protocol，切換實作零改動。"""

    name: str

    async def write_script(self, req: GenerationRequest) -> EngineResult: ...

    async def health(self) -> bool: ...

    async def aclose(self) -> None:
        """釋放底層資源（如 httpx client）。無資源者實作為 no-op。"""
        ...
