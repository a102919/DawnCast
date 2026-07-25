"""外部資料來源 providers。"""

from .base import CombinedProvider, SourceProvider
from .factory import make_source_provider
from .news import GdeltProvider
from .providers import (
    CrossrefProvider,
    FREDProvider,
    GoogleFactCheckProvider,
    InternetArchiveProvider,
    OpenAlexProvider,
    WorldBankProvider,
)
from .router import SourceRouter
from .search import TavilyProvider
from .wiki import WikipediaProvider

__all__ = [
    "CombinedProvider",
    "SourceProvider",
    "SourceRouter",
    "GdeltProvider",
    "TavilyProvider",
    "WikipediaProvider",
    "make_source_provider",
    "OpenAlexProvider",
    "CrossrefProvider",
    "WorldBankProvider",
    "FREDProvider",
    "GoogleFactCheckProvider",
    "InternetArchiveProvider",
]
