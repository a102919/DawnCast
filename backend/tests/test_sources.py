"""engine.sources 測試：全程 mock httpx transport，不打外部 API。"""

from __future__ import annotations

import httpx
import pytest

from engine.sources.base import CombinedProvider
from engine.sources.factory import make_source_provider
from engine.sources.news import GdeltProvider
from engine.sources.search import TavilyProvider
from engine.sources.wiki import WikipediaProvider
from shared.config import Settings
from shared.errors import SourceFetchError
from shared.models import SourceSnippet


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "tavily_api_key": "test-tavily-key",
        "wikipedia_user_agent": "DawnCast-Test/1.0 (test@example.com)",
        "source_max_snippets": 3,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def _swap_transport(client: httpx.AsyncClient, handler: httpx.MockTransport) -> None:
    client._transport = handler  # noqa: SLF001 測試替換底層 transport


# ── WikipediaProvider ────────────────────────────────────────


@pytest.mark.asyncio
async def test_wikipedia_provider_search_then_extract() -> None:
    calls: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        calls.append(params)
        if params.get("list") == "search":
            return httpx.Response(200, json={"query": {"search": [{"title": "Quantum computing"}]}})
        return httpx.Response(
            200,
            json={
                "query": {
                    "pages": {
                        "123": {
                            "title": "Quantum computing",
                            "extract": "Quantum computing uses qubits.",
                        }
                    }
                }
            },
        )

    provider = WikipediaProvider(_settings())
    _swap_transport(provider._client, httpx.MockTransport(handler))  # noqa: SLF001
    snippets = await provider.fetch("quantum computing")
    await provider.aclose()

    assert len(snippets) == 1
    assert snippets[0].id == "wiki:123"
    assert "qubits" in snippets[0].text
    assert len(calls) == 2  # 一次 search + 一次 extract


@pytest.mark.asyncio
async def test_wikipedia_provider_no_hits_returns_empty() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"query": {"search": []}})

    provider = WikipediaProvider(_settings())
    _swap_transport(provider._client, httpx.MockTransport(handler))  # noqa: SLF001
    snippets = await provider.fetch("obscure nonexistent topic")
    await provider.aclose()
    assert snippets == []


@pytest.mark.asyncio
async def test_wikipedia_provider_http_error_raises_source_fetch_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    provider = WikipediaProvider(_settings())
    _swap_transport(provider._client, httpx.MockTransport(handler))  # noqa: SLF001
    with pytest.raises(SourceFetchError):
        await provider.fetch("x")
    await provider.aclose()


# ── TavilyProvider ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_tavily_provider_maps_results_to_snippets() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/search"
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "Remote work trends",
                        "url": "https://example.com/a",
                        "content": "Remote work output rose.",
                        "published_date": "2026-07-01",
                    }
                ]
            },
        )

    provider = TavilyProvider(_settings())
    _swap_transport(provider._client, httpx.MockTransport(handler))  # noqa: SLF001
    snippets = await provider.fetch("remote work")
    await provider.aclose()

    assert len(snippets) == 1
    assert snippets[0].url == "https://example.com/a"
    assert snippets[0].published_at == "2026-07-01"


@pytest.mark.asyncio
async def test_tavily_provider_no_key_returns_empty_without_calling_api() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={"results": []})

    provider = TavilyProvider(_settings(tavily_api_key=""))
    _swap_transport(provider._client, httpx.MockTransport(handler))  # noqa: SLF001
    snippets = await provider.fetch("anything")
    await provider.aclose()
    assert snippets == []
    assert calls["n"] == 0


@pytest.mark.asyncio
async def test_tavily_provider_http_error_raises_source_fetch_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    provider = TavilyProvider(_settings())
    _swap_transport(provider._client, httpx.MockTransport(handler))  # noqa: SLF001
    with pytest.raises(SourceFetchError):
        await provider.fetch("x")
    await provider.aclose()


# ── GdeltProvider ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_gdelt_provider_without_tavily_key_uses_title_only() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "articles": [
                    {
                        "title": "AI regulation passes",
                        "url": "https://news.example.com/1",
                        "domain": "news.example.com",
                        "seendate": "20260714120000",
                    }
                ]
            },
        )

    provider = GdeltProvider(_settings(tavily_api_key=""))
    _swap_transport(provider._client, httpx.MockTransport(handler))  # noqa: SLF001
    assert provider._extractor is None  # 沒 key 不建 extractor
    snippets = await provider.fetch("AI regulation")
    await provider.aclose()

    assert len(snippets) == 1
    assert snippets[0].title == "AI regulation passes"
    assert "AI regulation passes" in snippets[0].text  # 退化成標題當內文


@pytest.mark.asyncio
async def test_gdelt_provider_http_error_raises_source_fetch_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    provider = GdeltProvider(_settings(tavily_api_key=""))
    _swap_transport(provider._client, httpx.MockTransport(handler))  # noqa: SLF001
    with pytest.raises(SourceFetchError):
        await provider.fetch("x")
    await provider.aclose()


# ── CombinedProvider ─────────────────────────────────────────


class _FakeProvider:
    def __init__(self, name: str, snippets: list[SourceSnippet] | None = None, error: bool = False):
        self.name = name
        self._snippets = snippets or []
        self._error = error
        self.closed = False

    async def fetch(self, query: str) -> list[SourceSnippet]:
        if self._error:
            raise SourceFetchError(f"{self.name} 掛了")
        return self._snippets

    async def aclose(self) -> None:
        self.closed = True


def _snippet(id_: str) -> SourceSnippet:
    return SourceSnippet(id=id_, title=id_, url=f"https://example.com/{id_}", text="x")


@pytest.mark.asyncio
async def test_combined_provider_merges_results() -> None:
    a = _FakeProvider("a", [_snippet("a1")])
    b = _FakeProvider("b", [_snippet("b1")])
    provider = CombinedProvider([a, b])
    snippets = await provider.fetch("q")
    assert [s.id for s in snippets] == ["a1", "b1"]


@pytest.mark.asyncio
async def test_combined_provider_one_failure_keeps_other_results() -> None:
    a = _FakeProvider("a", error=True)
    b = _FakeProvider("b", [_snippet("b1")])
    provider = CombinedProvider([a, b])
    snippets = await provider.fetch("q")
    assert [s.id for s in snippets] == ["b1"]


@pytest.mark.asyncio
async def test_combined_provider_aclose_closes_all() -> None:
    a = _FakeProvider("a")
    b = _FakeProvider("b")
    provider = CombinedProvider([a, b])
    await provider.aclose()
    assert a.closed and b.closed


# ── factory ─────────────────────────────────────────────────


def test_factory_dispatches_by_topic_type() -> None:
    settings = _settings()
    assert isinstance(make_source_provider("news", settings), GdeltProvider)
    assert isinstance(make_source_provider("product", settings), TavilyProvider)
    evergreen = make_source_provider("evergreen", settings)
    assert isinstance(evergreen, CombinedProvider)
    assert isinstance(evergreen._providers[0], WikipediaProvider)  # noqa: SLF001
    assert isinstance(evergreen._providers[1], TavilyProvider)  # noqa: SLF001
    assert make_source_provider("skill", settings) is None
