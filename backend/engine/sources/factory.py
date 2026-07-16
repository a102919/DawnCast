"""資料來源工廠：依 topic_type 選 provider。

對照使用者看到的三種入口（PRD 重新設計 §1）：
  news      → 今日新聞（GDELT）
  product   → 使用者指定主題（沿用既有 TopicType 值，語意重定義為「自訂主題」，走 Tavily）
  evergreen → 深度知識，Wikipedia + Tavily 都查，結果合併當 grounding（Wikipedia 免費但
              不夠即時，Tavily 補即時網路搜尋）
  skill     → 語言技能類內容（口語、慣用語）本質上教學導向、非事實導向，
              不需要外部 grounding，回 None 讓 retrieve_sources_node 跳過抓取。
"""

from __future__ import annotations

from shared.config import Settings, get_settings

from .base import CombinedProvider, SourceProvider
from .news import GdeltProvider
from .search import TavilyProvider
from .wiki import WikipediaProvider


def make_source_provider(
    topic_type: str, settings: Settings | None = None
) -> SourceProvider | None:
    cfg = settings or get_settings()
    if topic_type == "news":
        return GdeltProvider(cfg)
    if topic_type == "product":
        return TavilyProvider(cfg)
    if topic_type == "evergreen":
        return CombinedProvider([WikipediaProvider(cfg), TavilyProvider(cfg)])
    return None
