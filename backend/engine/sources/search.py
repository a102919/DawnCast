"""Tavily 資料來源：給「使用者指定主題」入口用的通用網路搜尋。

免費額度 1000 credits/月，商用條款未明講禁止但也未明講允許（見重新設計 plan
的條款查證）；正式上線前建議跟 Tavily 拿書面確認或直接上付費方案——這裡只留
設定開關，不寫死免費/付費邏輯。

/search 回應的 `content` 欄位本身就是清洗過、適合餵給 LLM 的摘要片段，
不需要額外呼叫 /extract 拿全文（省 credits）。
"""

from __future__ import annotations

import httpx

from shared.config import Settings, get_settings
from shared.errors import SourceFetchError
from shared.models import SourceSnippet


class TavilyProvider:
    """通用搜尋：使用者自訂主題入口用。"""

    name = "tavily"

    def __init__(self, settings: Settings | None = None) -> None:
        cfg = settings or get_settings()
        self._api_key = cfg.tavily_api_key
        self._max_snippets = cfg.source_max_snippets
        self._client = httpx.AsyncClient(
            base_url=cfg.tavily_base_url,
            timeout=httpx.Timeout(cfg.source_fetch_timeout),
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def fetch(self, query: str) -> list[SourceSnippet]:
        if not self._api_key:
            # 沒設 key：視同未啟用這條來源，回空 list 讓上層降級成純 LLM 生成。
            return []
        try:
            resp = await self._client.post(
                "/search",
                json={
                    "api_key": self._api_key,
                    "query": query,
                    "search_depth": "basic",
                    "max_results": self._max_snippets,
                },
            )
            resp.raise_for_status()
            data = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise SourceFetchError(f"Tavily 搜尋失敗：{type(exc).__name__}") from exc

        results = data.get("results", [])
        snippets: list[SourceSnippet] = []
        for i, r in enumerate(results[: self._max_snippets]):
            text = r.get("content")
            url = r.get("url")
            if not isinstance(text, str) or not text.strip() or not isinstance(url, str):
                continue
            snippets.append(
                SourceSnippet(
                    id=f"tavily:{i}",
                    title=r.get("title") or url,
                    url=url,
                    text=text.strip(),
                    published_at=r.get("published_date"),
                )
            )
        return snippets

    async def extract_urls(self, urls: list[str]) -> dict[str, str]:
        """給 GdeltProvider 補全文用：url -> 清洗過的正文。單一 API 失敗回空 dict。"""
        if not self._api_key or not urls:
            return {}
        try:
            resp = await self._client.post(
                "/extract", json={"api_key": self._api_key, "urls": urls}
            )
            resp.raise_for_status()
            data = resp.json()
        except (httpx.HTTPError, ValueError):
            return {}
        out: dict[str, str] = {}
        for r in data.get("results", []):
            url = r.get("url")
            content = r.get("raw_content")
            if isinstance(url, str) and isinstance(content, str) and content.strip():
                out[url] = content.strip()
        return out
