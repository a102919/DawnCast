"""真實資料來源 provider：news（GDELT）／search（Tavily）／wiki（Wikipedia）。"""

from __future__ import annotations

from .base import SourceProvider
from .factory import make_source_provider
from .news import GdeltProvider
from .search import TavilyProvider
from .wiki import WikipediaProvider

__all__ = [
    "GdeltProvider",
    "SourceProvider",
    "TavilyProvider",
    "WikipediaProvider",
    "make_source_provider",
]
