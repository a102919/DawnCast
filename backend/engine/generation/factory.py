"""引擎工廠：依 settings.generation_engine 選實作。

切引擎只改 env（GENERATION_ENGINE），呼叫端零改動（PRD §8 可逆性硬約束）。
"""

from __future__ import annotations

from collections.abc import Callable

from shared.config import Settings, get_settings
from shared.errors import ConfigError

from .api_key import ApiKeyEngine
from .base import GenerationEngine
from .claude_code import ClaudeCodeEngine
from .minimax import MinimaxEngine

# 引擎名稱 → 建構子。新增引擎只動這張表。
_REGISTRY: dict[str, Callable[[Settings], GenerationEngine]] = {
    "minimax": MinimaxEngine,
    "api_key": ApiKeyEngine,
    "claude_code": ClaudeCodeEngine,
}


def make_engine(settings: Settings | None = None) -> GenerationEngine:
    """依設定建出對應引擎；未知引擎名 raise ConfigError。"""
    cfg = settings or get_settings()
    builder = _REGISTRY.get(cfg.generation_engine)
    if builder is None:
        raise ConfigError(f"未知的 generation_engine：{cfg.generation_engine!r}")
    return builder(cfg)
