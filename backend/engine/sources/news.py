"""GDELT 資料來源：給「今日新聞」入口用的免費即時新聞事件。

GDELT DOC 2.0 API 免費、無需 key、每 15 分鐘更新，但回傳的是文章 metadata
（標題／URL／時間／來源網域），不含正文——正文需要額外一道抓取。

策略：GDELT 找出真實、有時效性的新聞候選 → 若有設定 Tavily key，用
TavilyProvider.extract_urls 補全文；沒設 key 時退化成「標題 + 網域」當
極簡 snippet（依然是真實、有日期的資料，只是內容較薄）。抓取失敗一律
不阻斷主流程（由 gather_evidence_node 兜底）。

GDELT 官方限流是「每 IP 每 5 秒 1 次請求」（見 _GDELT_MIN_INTERVAL_SECS
註解連結）。gather_evidence_node 對單集最多 6 個 sub-question 連發，沒節流
會直接撞 429 並被冷卻 15 分鐘。所以這層做 process 級滑動視窗，跨所有
GdeltProvider 實例共用一個鎖，呼叫端不用了解節流策略。
"""

from __future__ import annotations

import asyncio
import logging
import time

import httpx

from shared.config import Settings, get_settings
from shared.errors import SourceFetchError
from shared.models import SourceSnippet

from .base import _HttpSourceProvider
from .search import TavilyProvider

logger = logging.getLogger(__name__)

# GDELT 官方未公開精確 QPS，但實際觀察為每 5 秒 1 次請求；超過會 429 持續數分鐘。
# https://blog.gdeltproject.org/ukraine-api-rate-limiting-web-ngrams-3-0/
_GDELT_MIN_INTERVAL_SECS = 5.0
_gdelt_lock = asyncio.Lock()
# ponytail: 啟動時間戳的負偏移，讓首次 fetch 必走快路徑（避免程序剛啟動
# monotonic() 接近 0 時白白 sleep 5s）。改為 0.0 會誤判 wait ≈ 5s。
_gdelt_last_call_ts = -_GDELT_MIN_INTERVAL_SECS


class GdeltProvider(_HttpSourceProvider):
    """今日新聞入口用：GDELT 事件搜尋 + 選配的 Tavily 全文補全。"""

    name = "gdelt"

    def __init__(self, settings: Settings | None = None) -> None:
        cfg = settings or get_settings()
        super().__init__(
            base_url=cfg.gdelt_base_url, settings=cfg, read_timeout=cfg.source_fetch_timeout
        )
        self._max_snippets = cfg.source_max_snippets
        # 只有設了 Tavily key 才啟用全文補全；沒設就用純標題當 snippet。
        self._extractor = TavilyProvider(cfg) if cfg.tavily_api_key else None

    async def aclose(self) -> None:
        await super().aclose()
        if self._extractor is not None:
            await self._extractor.aclose()

    async def fetch(self, query: str) -> list[SourceSnippet]:
        global _gdelt_last_call_ts
        # 跨實例 process 級節流：每 5 秒最多一次打到 GDELT。
        # 持鎖時順便讀時鐘，避免兩個並發 coroutine 同步通過時間檢查。
        async with _gdelt_lock:
            now = time.monotonic()
            wait = _GDELT_MIN_INTERVAL_SECS - (now - _gdelt_last_call_ts)
            if wait > 0:
                logger.debug("GDELT 限流等待 %.2fs", wait)
                await asyncio.sleep(wait)
            _gdelt_last_call_ts = time.monotonic()
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
            if not isinstance(data, dict):
                raise ValueError(f"GDELT 回應非預期形狀：{type(data).__name__}")
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
