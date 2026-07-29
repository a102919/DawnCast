"""資料來源工廠：依 topic_type 選 provider。

對照使用者看到的三種入口（PRD 重新設計 §1）：
  news      → 今日新聞（GDELT）
  product   → 使用者指定主題（沿用既有 TopicType 值，語意重定義為「自訂主題」，走 Tavily，
              帶 recency_days 篩最近事件，避免撈到常青科普內容而非「這幾天發生的事」）
  evergreen → 深度知識，Wikipedia + Tavily 都查，結果合併當 grounding（Wikipedia 免費但
              不夠即時，Tavily 補即時網路搜尋）
  skill     → 語言技能類內容（口語、慣用語）本質上教學導向、非事實導向，
              不需要外部 grounding，回 None 讓 gather_evidence_node 跳過抓取。
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
        # GDELT 免費但 Zeabur api-ovate container outbound 對 gdeltproject.org
        # 不一定連得到（已實測 ConnectTimeout）。跟 evergreen 同模式：兩個並跑，
        # GDELT 連不上被 CombinedProvider 吞掉，Tavily news mode (topic=news +
        # days=tavily_recency_days) 補實際拿不到時的 fallback。修這條前 prod 跑
        # news 集數永遠是 provider_counts={}、grounded=false，podcast 變純 LLM
        # 生成無佐證。
        return CombinedProvider(
            [
                GdeltProvider(cfg),
                TavilyProvider(cfg, recency_days=cfg.tavily_recency_days),
            ]
        )
    if topic_type == "product":
        return TavilyProvider(cfg, recency_days=cfg.tavily_recency_days)
    if topic_type == "evergreen":
        return CombinedProvider([WikipediaProvider(cfg), TavilyProvider(cfg)])
    return None


__all__ = [
    "make_source_provider",
    "SourceProvider",
    "CombinedProvider",
]
