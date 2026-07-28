"""真實資料來源 provider 的共用契約。

鏡像 langgraph_pod 的 Protocol 模式：呼叫端只認 SourceProvider，
換供應商（news API、搜尋引擎、Wikipedia）零改動。
"""

from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable

import httpx

from shared.config import Settings
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


class _HttpSourceProvider:
    """走 httpx 的 provider 共用基底：統一 client 建構（connect/read timeout 分離）與 aclose()。

    connect/write/pool 沿用 settings.http_connect_timeout（5s 上線防呆），read 交給
    各 provider 自己的 fetch timeout 語意（例如 source_fetch_timeout），不可合併成單一
    數值——httpx.Timeout(單一數值) 等於四段都套同一個值，會讓連線逾時跟著被拉到 30s。
    """

    def __init__(
        self,
        base_url: str,
        settings: Settings,
        read_timeout: float,
        headers: dict[str, str] | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx.Timeout(
                connect=settings.http_connect_timeout,
                read=read_timeout,
                write=settings.http_connect_timeout,
                pool=settings.http_connect_timeout,
            ),
            headers=headers,
        )

    async def aclose(self) -> None:
        await self._client.aclose()


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
            try:
                await provider.aclose()
            except Exception as exc:
                logger.warning("combined source %s 關閉失敗，跳過: %s", provider.name, exc)
