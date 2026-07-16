"""真實資料來源 provider 的共用契約。

鏡像 engine.generation.base 的 Protocol 模式：呼叫端只認 SourceProvider，
換供應商（news API、搜尋引擎、Wikipedia）零改動。
"""

from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable

from shared.errors import SourceFetchError
from shared.models import SourceSnippet

logger = logging.getLogger(__name__)


@runtime_checkable
class SourceProvider(Protocol):
    """資料來源介面。fetch 失敗一律 raise SourceFetchError，由呼叫端決定降級。"""

    name: str

    async def fetch(self, query: str) -> list[SourceSnippet]: ...

    async def aclose(self) -> None:
        """釋放底層資源（如 httpx client）。無資源者實作為 no-op。"""
        ...


class CombinedProvider:
    """合併多個 provider 的結果一起餵給 LLM。單一 provider 失敗降級跳過，不擋其他來源。"""

    name = "combined"

    def __init__(self, providers: list[SourceProvider]) -> None:
        self._providers = providers

    async def fetch(self, query: str) -> list[SourceSnippet]:
        snippets: list[SourceSnippet] = []
        for provider in self._providers:
            try:
                snippets.extend(await provider.fetch(query))
            except SourceFetchError as exc:
                logger.warning("combined source %s 抓取失敗，跳過: %s", provider.name, exc)
        return snippets

    async def aclose(self) -> None:
        for provider in self._providers:
            await provider.aclose()
