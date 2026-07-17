"""邊界層：用 pydantic-settings 一次 parse 所有環境變數。

禁止在 module 頂層讀 os.environ 散落各處；所有設定都收斂在這裡。
FastAPI 與 worker 共用同一份 Settings。
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from shared.errors import ConfigError

EngineName = Literal["minimax", "api_key", "claude_code"]
FailoverMode = Literal["degrade", "failover"]
Environment = Literal["dev", "prod"]

# JWT secret 預設哨兵值：絕不可在 prod 用它驗證真實 token。
_DEFAULT_JWT_SECRET = "dev-secret-change-me"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── 執行環境 ───────────────────────────────────────────
    # 預設 dev（本機 / 測試免設定）。部署務必設 ENVIRONMENT=prod，
    # 才會觸發 assert_secure() 的上線防呆（見下）。
    environment: Environment = "dev"

    # 允許的前端 origin（CORS）。prod 由 env 帶入真實網域，禁止 '*'。
    cors_allowed_origins: list[str] = Field(
        default=["http://localhost:5173", "http://127.0.0.1:5173"],
    )

    # ── 資料庫（Supabase 託管 Postgres）──────────────────────
    database_url: str = Field(
        default="postgresql://postgres:postgres@localhost:5432/postgres",
        description="psycopg3 連線字串（service-role 連線，FastAPI 持有）",
    )
    db_pool_min: int = 1
    db_pool_max: int = 10

    # ── Auth（Supabase Auth 發的 JWT）────────────────────────
    supabase_jwt_secret: str = Field(
        default=_DEFAULT_JWT_SECRET,
        description="驗 Supabase JWT 用（HS256）",
    )
    supabase_jwt_audience: str = "authenticated"

    # Ops/admin endpoint（T7）驗證用固定 token，走 X-Admin-Token header 比對。
    # 不可硬寫在程式碼；空字串 = 未設定，prod 會被 assert_secure() 擋下。
    admin_token: str = ""

    # ── 生成引擎（PRD §8，env 一鍵切）─────────────────────────
    generation_engine: EngineName = "api_key"
    failover_mode: FailoverMode = "degrade"

    # api_key fallback（MiniMax M2.5 / Anthropic 按量）
    api_base_url: str = "https://api.minimax.io/anthropic"
    api_key: str = ""
    api_model: str = "MiniMax-M2.5"

    # minimax 主引擎（OpenClaw 訂閱 token；Anthropic 相容 endpoint）
    minimax_anthropic_base_url: str = "https://api.minimax.io/anthropic"
    minimax_auth_token: str = ""
    minimax_model: str = "MiniMax-M2.5"

    # 嵌入（V2 才接主流程，MVP 留設定）
    embedding_base_url: str = "https://api.openai.com/v1"
    embedding_api_key: str = ""
    embedding_model: str = "text-embedding-3-small"
    embedding_dim: int = 512

    # 聚類門檻（V2 啟用，上線前真實校準；絕不拍 0.85）
    cluster_threshold_same_lang: float = 0.50
    cluster_threshold_cross_lang: float = 0.45

    # ── 外部 HTTP 邊界（安全規範）────────────────────────────
    http_connect_timeout: float = 5.0
    http_read_timeout: float = 30.0
    http_max_retries: int = 3
    # 寫稿語意層重試硬上限（PRD §6 防重生風暴）
    generation_max_attempts: int = 3

    # ── Rate limit（T5）──────────────────────────────────────
    # /dict/lookup 每分鐘每 client 允許的查詢次數（單一 process 的 in-memory 限制；
    # 多 worker 部署下實際上限 = N × 此值，spec 排除 Redis 故採 in-memory）。
    rate_limit_dict_per_min: int = 60

    # ── Cloudflare R2（S3 相容）─────────────────────────────
    r2_account_id: str = ""
    r2_access_key_id: str = ""
    r2_secret_access_key: str = ""
    r2_bucket: str = "dawncast"
    r2_endpoint: str = ""  # https://<account>.r2.cloudflarestorage.com
    r2_signed_url_ttl: int = 7200  # 2h，避免長音檔播一半過期

    # ── Piper TTS（詞卡發音喇叭）──────────────────────────────
    # 語音模型檔路徑；空字串 = 未設定，_resolve_model() fallback 到
    # ~/.local/share/piper/en_US-amy-medium.onnx（見 engine/media/dict_audio.py）。
    piper_voice_model: str = ""

    # 本機 fallback（無 R2 時讓前端能拿到媒體檔）。
    # 設了路徑 + 路徑存在 → backend mount /media/* StaticFiles；
    # 沒設 / 設空字串 → get_episode_url 維持 raise NotFoundError。
    local_media_dir: str = ""
    public_base_url: str = "http://localhost:8000"

    # dev 用 auth bypass：env 顯式開 + 標 dev 環境 + Authorization 為 'Bearer dev' 或缺
    # → 直接用 dev_user_id 當 sub。prod 不開；上線前從 .env 移除這兩個欄位。
    dev_auth_bypass: bool = False
    dev_user_id: str = ""

    # ── 批次 / worker ──────────────────────────────────────
    # 夜間批次的日曆日錨點時區。worker 不用容器本機時間（通常 UTC），
    # 一律以此時區算「今天」，與前端 user tz 寫入的 order_date 對齊。
    app_timezone: str = "Asia/Taipei"
    job_timeout_sec: int = 8 * 60
    dead_letter_after: int = 3
    pause_sec: float = 0.3
    # chapter/話題轉換邊界的停頓（ScriptLine.pause_before=True 時套用）。
    long_pause_sec: float = 0.7
    sample_rate: int = 24000

    # ── 寫稿品質（LangGraph pod 用）────────────────────────
    # 寫稿引擎預設用的 CEFR 等級（pod 會帶進 prompt）
    cefr_level: str = "B1"

    # LLM-as-judge 三軸都要達到的門檻（0-1 per axis）
    quality_threshold: float = 0.6

    # judge 不及格 → 觸發 rewrite 的最大次數（cycle cap）
    max_rewrite_iterations: int = 2

    # topic_type → 寫稿 tone（tone 寫進 prompt）
    tone_map: dict[str, str] = {
        "news": "curious",
        "evergreen": "playful",
        "skill": "contemplative",
        "product": "debate",
    }

    # ── 真實資料來源（retrieve_sources_node 用，PRD 重新設計 §1）───
    # 抓取失敗一律降級成空 sources（不阻斷生成），故這裡沒有「必填」欄位；
    # 沒設 key 的 provider 在 factory 裡直接跳過。
    tavily_api_key: str = ""
    tavily_base_url: str = "https://api.tavily.com"
    gdelt_base_url: str = "https://api.gdeltproject.org/api/v2/doc/doc"
    # 用 MediaWiki Action API（穩定、文件完整）而非 REST summary endpoint，
    # 同一支 URL 同時做 srsearch（找標題）與 prop=extracts（拿內文）。
    wikipedia_base_url: str = "https://en.wikipedia.org/w/api.php"
    # Wikimedia API 政策要求可識別的 User-Agent（含聯絡方式），見
    # https://foundation.wikimedia.org/wiki/Policy:Wikimedia_Foundation_User-Agent_Policy
    wikipedia_user_agent: str = "DawnCast/1.0 (https://dawncast.app; contact: ops@dawncast.app)"
    source_fetch_timeout: float = 10.0
    source_max_snippets: int = 5

    def assert_secure(self) -> None:
        """上線防呆：prod 環境下拒絕不安全設定，啟動即 fail（fail closed）。

        dev / 測試不檢查（預設值即可跑）。prod 必檢：
          1. JWT secret 不可是預設哨兵或空字串——否則攻擊者可自簽任意 sub 繞過授權。
          2. CORS 不可用萬用 '*'（搭配 allow_credentials 會憑證外洩）。
        """
        if self.environment != "prod":
            return
        if self.supabase_jwt_secret in ("", _DEFAULT_JWT_SECRET):
            raise ConfigError("prod 未設定 SUPABASE_JWT_SECRET（不可用預設值）")
        if "*" in self.cors_allowed_origins:
            raise ConfigError("prod 的 CORS_ALLOWED_ORIGINS 不可包含 '*'")
        if self.admin_token == "":
            raise ConfigError("prod 未設定 ADMIN_TOKEN（不可用空字串）")


@lru_cache
def get_settings() -> Settings:
    return Settings()
