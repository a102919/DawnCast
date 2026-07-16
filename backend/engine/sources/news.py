"""GDELT 資料來源：給「今日新聞」入口用的免費即時新聞事件。

GDELT DOC 2.0 API 免費、無需 key、每 15 分鐘更新，但回傳的是文章 metadata
（標題／URL／時間／來源網域），不含正文——正文需要額外一道抓取。

策略：GDELT 找出真實、有時效性的新聞候選 → 若有設定 Tavily key，用
TavilyProvider.extract_urls 補全文；沒設 key 時退化成「標題 + 網域」當
極簡 snippet（依然是真實、有日期的資料，只是內容較薄）。抓取失敗一律
不阻斷主流程（由 retrieve_sources_node 兜底）。
"""

from __future__ import annotations

import httpx

from shared.config import Settings, get_settings
from shared.errors import SourceFetchError
from shared.models import SourceSnippet

from .search import TavilyProvider


class GdeltProvider:
    """今日新聞入口用：GDELT 事件搜尋 + 選配的 Tavily 全文補全。"""

    name = "gdelt"

    def __init__(self, settings: Settings | None = None) -> None:
        cfg = settings or get_settings()
        self._max_snippets = cfg.source_max_snippets
        self._client = httpx.AsyncClient(
            base_url=cfg.gdelt_base_url,
            timeout=httpx.Timeout(cfg.source_fetch_timeout),
        )
        # 只有設了 Tavily key 才啟用全文補全；沒設就用純標題當 snippet。
        self._extractor = TavilyProvider(cfg) if cfg.tavily_api_key else None

    async def aclose(self) -> None:
        await self._client.aclose()
        if self._extractor is not None:
            await self._extractor.aclose()

    async def fetch(self, query: str) -> list[SourceSnippet]:
        try:
            resp = await self._client.get(
                "",
                params={
                    "query": query,
                    "mode": "artlist",
                    "format": "json",
                    "maxrecords": self._max_snippets,
                    "sort": "hybridrel",
                },
            )
            resp.raise_for_status()
            data = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise SourceFetchError(f"GDELT 搜尋失敗：{type(exc).__name__}") from exc

        articles = data.get("articles", [])[: self._max_snippets]
        urls = [a["url"] for a in articles if isinstance(a.get("url"), str)]
        full_text = await self._extractor.extract_urls(urls) if self._extractor else {}

        snippets: list[SourceSnippet] = []
        for i, a in enumerate(articles):
            url = a.get("url")
            title = a.get("title")
            if not isinstance(url, str) or not isinstance(title, str):
                continue
            text = full_text.get(url) or f"{title}（{a.get('domain', '')}）"
            snippets.append(
                SourceSnippet(
                    id=f"gdelt:{i}",
                    title=title,
                    url=url,
                    text=text,
                    published_at=a.get("seendate"),
                )
            )
        return snippets
