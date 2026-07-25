from __future__ import annotations

import html
import re

import httpx

from shared.config import Settings, get_settings
from shared.models import SourceSnippet

from .http import (
    ArchiveResponse,
    CrossrefResponse,
    FactResponse,
    FREDResponse,
    OpenAlexResponse,
    WBItem,
    request_json,
    validate,
)


class _HttpProvider:
    def __init__(self, settings: Settings, base_url: str) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx.Timeout(
                connect=settings.http_connect_timeout,
                read=settings.source_fetch_timeout,
                write=30,
                pool=5,
            ),
        )
        self._retries = min(settings.http_max_retries, 3)
        self._max = settings.source_max_snippets

    async def aclose(self) -> None:
        await self._client.aclose()


class OpenAlexProvider(_HttpProvider):
    name = "openalex"

    def __init__(self, settings: Settings | None = None):
        s = settings or get_settings()
        super().__init__(s, s.openalex_base_url)
        self._email = s.openalex_email

    async def fetch(self, query: str) -> list[SourceSnippet]:
        data = validate(
            await request_json(
                self._client,
                "GET",
                "/works",
                retries=self._retries,
                params={"search": query, "per-page": self._max, "mailto": self._email},
            ),
            OpenAlexResponse,
        )
        out: list[SourceSnippet] = []
        for w in data.results:
            text = w.title or ""
            url = w.doi or w.id
            if text:
                out.append(
                    SourceSnippet(
                        id=f"openalex:{w.id.rsplit('/', 1)[-1]}",
                        title=text,
                        url=url,
                        text=text,
                        published_at=w.publication_date,
                    )
                )
        return out


class CrossrefProvider(_HttpProvider):
    name = "crossref"

    def __init__(self, settings: Settings | None = None):
        s = settings or get_settings()
        super().__init__(s, s.crossref_base_url)

    async def fetch(self, query: str) -> list[SourceSnippet]:
        d = validate(
            await request_json(
                self._client,
                "GET",
                "/works",
                retries=self._retries,
                params={"query": query, "rows": self._max},
            ),
            CrossrefResponse,
        )
        items = d.message.get("items", [])
        out: list[SourceSnippet] = []
        for i in items if isinstance(items, list) else []:
            if not isinstance(i, dict):
                continue
            title = (i.get("title") or [""])[0]
            url = i.get("URL") or "https://doi.org/" + str(i.get("DOI", ""))
            text = re.sub("<[^>]+>", "", str(i.get("abstract") or title))
            if title:
                out.append(
                    SourceSnippet(
                        id="crossref:" + str(i.get("DOI", len(out))),
                        title=title,
                        url=url,
                        text=html.unescape(text),
                    )
                )
        return out


class WorldBankProvider(_HttpProvider):
    name = "world_bank"

    def __init__(self, settings: Settings | None = None):
        s = settings or get_settings()
        super().__init__(s, s.world_bank_base_url)
        self._indicator = s.world_bank_indicator

    async def fetch(self, query: str) -> list[SourceSnippet]:
        d = await request_json(
            self._client,
            "GET",
            f"/country/{query}/indicator/{self._indicator}",
            retries=self._retries,
            params={"format": "json", "per_page": self._max},
        )
        rows = d[1] if isinstance(d, list) and len(d) > 1 else []
        out: list[SourceSnippet] = []
        for r in rows if isinstance(rows, list) else []:
            item = validate(r, WBItem)
            title = (item.indicator or {}).get("value", "World Bank")
            out.append(
                SourceSnippet(
                    id=f"worldbank:{item.date}",
                    title=title,
                    url="https://data.worldbank.org/",
                    text=f"{title}: {item.value} ({item.date})",
                    published_at=item.date,
                )
            )
        return out


class FREDProvider(_HttpProvider):
    name = "fred"

    def __init__(self, settings: Settings | None = None):
        s = settings or get_settings()
        super().__init__(s, s.fred_base_url)
        self._key = s.fred_api_key

    async def fetch(self, query: str) -> list[SourceSnippet]:
        if not self._key:
            return []
        d = validate(
            await request_json(
                self._client,
                "GET",
                "/series/observations",
                retries=self._retries,
                params={
                    "series_id": query,
                    "api_key": self._key,
                    "file_type": "json",
                    "limit": self._max,
                },
            ),
            FREDResponse,
        )
        return [
            SourceSnippet(
                id=f"fred:{o.get('date')}",
                title=query,
                url="https://fred.stlouisfed.org/",
                text=f"{o.get('date')}: {o.get('value')}",
                published_at=o.get("date"),
            )
            for o in d.observations
        ]


class GoogleFactCheckProvider(_HttpProvider):
    name = "google_fact_check"

    def __init__(self, settings: Settings | None = None):
        s = settings or get_settings()
        super().__init__(s, s.fact_check_base_url)
        self._key = s.google_fact_check_api_key

    async def fetch(self, query: str) -> list[SourceSnippet]:
        if not self._key:
            return []
        d = validate(
            await request_json(
                self._client,
                "GET",
                "/v1alpha1/claims:search",
                retries=self._retries,
                params={"query": query, "key": self._key, "pageSize": self._max},
            ),
            FactResponse,
        )
        out: list[SourceSnippet] = []
        for c in d.claims:
            text = str(c.get("text", ""))
            if text:
                out.append(
                    SourceSnippet(
                        id="factcheck:" + str(len(out)),
                        title=text,
                        url="https://toolbox.google.com/factcheck/",
                        text=text,
                    )
                )
        return out


class InternetArchiveProvider(_HttpProvider):
    name = "internet_archive"

    def __init__(self, settings: Settings | None = None):
        s = settings or get_settings()
        super().__init__(s, s.internet_archive_base_url)

    async def fetch(self, query: str) -> list[SourceSnippet]:
        d = validate(
            await request_json(
                self._client,
                "GET",
                "/advancedsearch.php",
                retries=self._retries,
                params={
                    "q": query,
                    "fl[]": ["identifier", "title", "description", "date"],
                    "rows": self._max,
                    "output": "json",
                },
            ),
            ArchiveResponse,
        )
        docs = d.response.get("docs", [])
        out: list[SourceSnippet] = []
        for x in docs if isinstance(docs, list) else []:
            if isinstance(x, dict):
                title = str(x.get("title") or x.get("identifier"))
                ident = str(x.get("identifier"))
                out.append(
                    SourceSnippet(
                        id="archive:" + ident,
                        title=title,
                        url="https://archive.org/details/" + ident,
                        text=str(x.get("description") or title),
                        published_at=str(x.get("date")) if x.get("date") else None,
                    )
                )
        return out
