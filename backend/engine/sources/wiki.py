"""Wikipedia 資料來源：MediaWiki Action API（穩定、免費、CC-BY-SA 可商用）。

兩段呼叫：
  1. list=search 找最符合 query 的頁面標題（query 常是中文或口語題目，
     不一定精確對應條目標題）。
  2. prop=extracts 用該標題拿純文字內文（explaintext=1 去 wiki markup）。

依 Wikimedia API Usage Guidelines 要求帶可識別 User-Agent（含聯絡方式）。
"""

from __future__ import annotations

import httpx

from shared.config import Settings, get_settings
from shared.errors import SourceFetchError
from shared.models import SourceSnippet


class WikipediaProvider:
    """深度知識入口用：抓 Wikipedia 條目摘要當 grounding 素材。"""

    name = "wikipedia"

    def __init__(self, settings: Settings | None = None) -> None:
        cfg = settings or get_settings()
        self._max_snippets = cfg.source_max_snippets
        self._client = httpx.AsyncClient(
            base_url=cfg.wikipedia_base_url,
            timeout=httpx.Timeout(cfg.source_fetch_timeout),
            headers={"User-Agent": cfg.wikipedia_user_agent},
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _search_titles(self, query: str) -> list[str]:
        resp = await self._client.get(
            "",
            params={
                "action": "query",
                "list": "search",
                "srsearch": query,
                "srlimit": self._max_snippets,
                "format": "json",
            },
        )
        resp.raise_for_status()
        data = resp.json()
        hits = data.get("query", {}).get("search", [])
        return [h["title"] for h in hits if isinstance(h.get("title"), str)]

    async def _extract(self, title: str) -> SourceSnippet | None:
        resp = await self._client.get(
            "",
            params={
                "action": "query",
                "prop": "extracts",
                "exintro": 1,
                "explaintext": 1,
                "titles": title,
                "format": "json",
            },
        )
        resp.raise_for_status()
        data = resp.json()
        pages = data.get("query", {}).get("pages", {})
        for page_id, page in pages.items():
            if page_id == "-1":  # 找不到該頁
                continue
            extract = page.get("extract")
            if isinstance(extract, str) and extract.strip():
                page_title = page.get("title", title)
                url = f"https://en.wikipedia.org/wiki/{page_title.replace(' ', '_')}"
                return SourceSnippet(
                    id=f"wiki:{page_id}",
                    title=page_title,
                    url=url,
                    text=extract.strip(),
                    published_at=None,  # Wikipedia 條目沒有單一發布日
                )
        return None

    async def fetch(self, query: str) -> list[SourceSnippet]:
        try:
            titles = await self._search_titles(query)
            snippets: list[SourceSnippet] = []
            for title in titles[: self._max_snippets]:
                snippet = await self._extract(title)
                if snippet is not None:
                    snippets.append(snippet)
            return snippets
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            raise SourceFetchError(f"Wikipedia 抓取失敗：{type(exc).__name__}") from exc
