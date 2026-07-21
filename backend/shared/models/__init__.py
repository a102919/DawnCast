"""pydantic 契約：FastAPI 與 worker 共用的資料結構。

兩類（各自一檔，這裡統一 re-export，呼叫端 import 路徑不變）：
1. 引擎契約（engine.py）— snake_case，對齊 script JSON ground truth。
2. API 契約（api.py）— camelCase alias，鏡像 frontend/src/api/types.ts。
"""

from shared.models.api import (
    AccountInfo,
    Activity,
    AdminEpisode,
    AdminJobQueue,
    AdminTokenUsageItem,
    AdminTokenUsageResponse,
    CamelModel,
    Cue,
    DailyOrder,
    DailyOrderStatus,
    DictEntry,
    Episode,
    EpisodeListItem,
    Settings,
    VocabItem,
)
from shared.models.engine import (
    ANGLES,
    EntryMode,
    FreshnessClass,
    JudgeVerdict,
    LengthTier,
    ScriptFormat,
    ScriptJSON,
    ScriptLine,
    SourcedFact,
    SourceSnippet,
    Speaker,
    TargetVocab,
    TopicType,
)

__all__ = [
    "ANGLES",
    "AccountInfo",
    "Activity",
    "AdminEpisode",
    "AdminJobQueue",
    "AdminTokenUsageItem",
    "AdminTokenUsageResponse",
    "CamelModel",
    "Cue",
    "DailyOrder",
    "DailyOrderStatus",
    "DictEntry",
    "Episode",
    "EpisodeListItem",
    "EntryMode",
    "FreshnessClass",
    "JudgeVerdict",
    "LengthTier",
    "ScriptFormat",
    "ScriptJSON",
    "ScriptLine",
    "Settings",
    "SourceSnippet",
    "SourcedFact",
    "Speaker",
    "TargetVocab",
    "TopicType",
    "VocabItem",
]
