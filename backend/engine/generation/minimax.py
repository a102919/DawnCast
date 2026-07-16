"""MiniMax 主引擎（OpenClaw 訂閱 token，Anthropic 相容 endpoint）。

與 ApiKeyEngine 同構，只是連線參數不同。HTTP + 解析 + 重試邏輯全繼承自
_AnthropicCompatEngine，不重複實作。
"""

from __future__ import annotations

from shared.config import Settings, get_settings

from .api_key import _AnthropicCompatEngine
from .base import GenerationEngine


class MinimaxEngine(_AnthropicCompatEngine):
    """主引擎：用訂閱 token 打 MiniMax 的 Anthropic 相容 endpoint。"""

    name = "minimax"

    def __init__(self, settings: Settings | None = None) -> None:
        cfg = settings or get_settings()
        super().__init__(
            base_url=cfg.minimax_anthropic_base_url,
            auth_token=cfg.minimax_auth_token,
            model=cfg.minimax_model,
            settings=cfg,
        )


_: type[GenerationEngine] = MinimaxEngine
