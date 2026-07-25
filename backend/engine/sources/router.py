from __future__ import annotations

from shared.config import Settings, get_settings

from .base import SourceProvider
from .factory import make_source_provider


class SourceRouter:
    """依來源識別碼建立 provider；未知來源安全回傳 ``None``。"""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    def route(self, source: str) -> SourceProvider | None:
        return make_source_provider(source, self._settings)
