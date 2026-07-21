"""邊界層：用 pydantic-settings 一次 parse 所有環境變數。

禁止在 module 頂層讀 os.environ 散落各處；所有設定都收斂在這裡。
FastAPI 與 worker 共用同一份 Settings。
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic_settings.sources.types import NoDecode

from shared.errors import ConfigError

EngineName = Literal["minimax", "api_key"]
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
    # ponytail: NoDecode 跳過 pydantic-settings 預設對 list 走的 json.loads —
    # Zeabur 變數面板會 strip 字串引號，餵 JSON list 進容器會壞掉；改用純字串餵
    # + before validator 兩種形式都收。同時驗證看 test_cors_middleware 仍預期 list[str]。
    cors_allowed_origins: Annotated[list[str], NoDecode] = Field(
        default=["http://localhost:5173", "http://127.0.0.1:5173"],
    )

    @field_validator("cors_allowed_origins", mode="before")
    @classmethod
    def _coerce_cors(cls, v: object) -> object:
        if isinstance(v, str):
            stripped = v.strip()
            if stripped.startswith("["):
                return json.loads(stripped)  # JSON list 形式（dev .env）
            return [s.strip() for s in stripped.split(",") if s.strip()]
        return v

    # origin regex 補充（e.g. devtunnels 子網域）。空字串表示不啟用。
    # 預設空：fail-secure；prod 帶值會被 assert_secure() 拒絕，dev 想用請於
    # 本機 .env 顯式設定 CORS_ALLOWED_ORIGIN_REGEX（dotenv 格式，不寫 Python r""）。
    cors_allowed_origin_regex: str = Field(
        default="",
    )

    # ── 資料庫 ─────────────────────────────────────────────
    # Cloud 跟 Self-host 共用同一條連線字串格式：postgres://<user>:<pwd>@<host>:<port>/<db>
    # - Cloud Supabase：service-role → user 用 `postgres.{project_ref}` 走連線池。
    # - Zeabur Self-host：user 用 `postgres`；內網 host = `db`（Docker service name）。
    # 兩種 case 都靠 DATABASE_URL 環境變數覆蓋；default 只給本機 docker-compose 用。
    database_url: str = Field(
        default="postgresql://postgres:postgres@localhost:5432/postgres",
        description="psycopg3 連線字串（service-role 連線，FastAPI 持有）",
    )
    db_pool_min: int = 1
    db_pool_max: int = 10

    # ── Auth（Supabase Auth 發的 JWT）────────────────────────
    # ES256（ECC P-256）簽 JWT。
    # - Cloud Supabase：JWKS 從 Supabase project URL 直接拿。
    # - Zeabur Self-host：透過 Kong 的 /auth/v1/.well-known/jwks.json 對外，
    #   完整 URL 為 https://<API_EXTERNAL_URL>/auth/v1/.well-known/jwks.json。
    supabase_jwks_url: str = Field(
        default="https://agrprhsbfnxzwyugctrp.supabase.co/auth/v1/.well-known/jwks.json",
        description="JWKS endpoint 網址；驗 ES256 token 用",
    )
    supabase_jwt_audience: str = "authenticated"
    # 保留欄位向後相容舊測試 / 工具腳本；prod 不再用。
    supabase_jwt_secret: str = Field(
        default=_DEFAULT_JWT_SECRET,
        description="（legacy）HS256 對稱 secret；ES256 時代已不驗 token 用，僅測試相容",
    )

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

    # ── MiniMax speech TTS（同一顆訂閱 token；已實測 t2a_v2 可用）──────
    # token 未設或呼叫失敗時整份腳本 fallback 到 edge-tts（見 media/tts.py）。
    minimax_tts_url: str = "https://api.minimax.io/v1/t2a_v2"
    minimax_tts_model: str = "speech-02-turbo"

    # ── 外部 HTTP 邊界（安全規範）────────────────────────────
    http_connect_timeout: float = 5.0
    http_read_timeout: float = 30.0
    http_max_retries: int = 3

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
          1. JWKS URL 不可是空字串——否則無法驗 token。
          2. CORS 不可用萬用 '*'（搭配 allow_credentials 會憑證外洩）。
          3. devtunnels regex 不可帶到 prod——會放行任意 devtunnel 子網域。
             main.py 的 middleware 也會在 prod 跳過此設定，雙重保險。
        """
        if self.environment != "prod":
            return
        if not self.supabase_jwks_url:
            raise ConfigError("prod 未設定 SUPABASE_JWKS_URL（不可為空）")
        if "*" in self.cors_allowed_origins:
            raise ConfigError("prod 的 CORS_ALLOWED_ORIGINS 不可包含 '*'")
        if self.cors_allowed_origin_regex.strip():
            raise ConfigError("prod 的 CORS_ALLOWED_ORIGIN_REGEX 不可設定（dev-only；prod 留空）")
        if self.admin_token == "":
            raise ConfigError("prod 未設定 ADMIN_TOKEN（不可用空字串）")


@lru_cache
def get_settings() -> Settings:
    return Settings()
