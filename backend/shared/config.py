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
    # 預設空字串（比照 admin_email）：不可硬寫成任何一個特定 Supabase 專案的網址——
    # 那樣 assert_secure() 的「非空即通過」判斷形同虛設，部署忘記設定也會被誤判成安全。
    # 空字串 = 未設定，prod 會被 assert_secure() 擋下；dev / 測試請於 .env 顯式設定。
    supabase_jwks_url: str = Field(
        default="",
        description="JWKS endpoint 網址；驗 ES256 token 用",
    )
    supabase_jwt_audience: str = "authenticated"
    # 保留欄位向後相容舊測試 / 工具腳本；prod 不再用。
    supabase_jwt_secret: str = Field(
        default=_DEFAULT_JWT_SECRET,
        description="（legacy）HS256 對稱 secret；ES256 時代已不驗 token 用，僅測試相容",
    )

    # JWT 簽章模式選擇 — 給 self-host 用的 opt-in HS256 path。
    # default "ES256"：跟 cloud Supabase 一致，後端從 JWKS 拿公鑰。
    # "HS256"：自架 gotrue（無 ES256 公開 JWKS）時設，後端用 supabase_jwt_secret 直接 verify。
    # 切到 HS256 path 後從 assert_secure() 拿掉 prod 必設 JWKS URL 的條款 — 見 deps.py。
    supabase_jwt_alg: Literal["ES256", "HS256"] = "ES256"

    # admin 驗證唯一路徑：既有 Supabase JWT（Google 登入）email claim 白名單。
    # 用已登入的 Google 帳號就能開後台，不用每次手動複製貼上 token。單一 email
    # （YAGNI：目前只有單一管理員，見 admin.py 註解）；不可硬寫在程式碼，空字串
    # = 未設定，prod 會被 assert_secure() 擋下。X-Admin-Token 後門已在 2026-07-29
    # 砍掉：常駐 env 字串一旦洩漏就成永久後門，email 白名單才是對得起「單一管理員」
    # 這個事實的設計。
    admin_email: str = ""

    # ── 生成引擎（PRD §8，env 一鍵切）─────────────────────────
    generation_engine: EngineName = "api_key"
    failover_mode: FailoverMode = "degrade"

    # api_key fallback（MiniMax M3 / Anthropic 按量）
    api_base_url: str = "https://api.minimax.io/anthropic"
    api_key: str = ""
    api_model: str = "MiniMax-M3"

    # minimax 主引擎（OpenClaw 訂閱 token；Anthropic 相容 endpoint）
    minimax_anthropic_base_url: str = "https://api.minimax.io/anthropic"
    minimax_auth_token: str = ""
    minimax_model: str = "MiniMax-M3"

    # ── MiniMax speech TTS（同一顆訂閱 token；已實測 t2a_v2 可用）──────
    # token 未設或呼叫失敗時整份腳本 fallback 到 edge-tts（見 media/tts.py）。
    minimax_tts_url: str = "https://api.minimax.io/v1/t2a_v2"
    minimax_tts_model: str = "speech-02-turbo"

    # ── 外部 HTTP 邊界（安全規範）────────────────────────────
    http_connect_timeout: float = 5.0
    # 180s 給 LLM thinking mode（MiniMax M3 + 4k reasoning budget + 16k script）
    # 留反應空間；短 timeout 會把正常 LLM call 視為 ReadTimeout。詳 chat.py read_timeout。
    http_read_timeout: float = 180.0
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

    # Worker proxy URL（Safari audio Range/416 fix）。有設 → presigned_get_url(s)
    # 改回 Worker host + key（不簽章）；沒設 → 維持 R2 presign 行為（dev/本機）。
    # 設成完整 https URL（含 https://、worker.dev 子網域），尾端不加 /。
    audio_proxy_base_url: str = ""

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
    # 拉高到 900：分段生成（outline 1 次 + N 段連續呼叫）總耗時必然
    # 比單段更長，且每段 LLM 連續 round 觸發 retry 時還會再拉長
    # （真實 Medium B1 單 writer 已測到 474s，逼近舊值 480s）；
    # worker.py GENERATE_VT 同時調高以維持底下註解的不變式。
    job_timeout_sec: int = 15 * 60
    dead_letter_after: int = 3
    # 同時處理的 generate job 數上限（worker.py run_worker）。本地測試過
    # MiniMax TTS 32 併發零失敗（見 scripts/probe_minimax_concurrency.py），
    # 5 是保守值，留安全邊際給共用額度池的其他呼叫。
    generate_max_concurrency: int = Field(default=5, ge=1)
    pause_sec: float = 0.3
    # chapter/話題轉換邊界的停頓（ScriptLine.pause_before=True 時套用）。
    long_pause_sec: float = 0.7
    sample_rate: int = 24000

    # ── 頻道（Channel）機制 ──────────────────────────────────
    # 每日最多產幾集：成本天花板。pick_daily_topics 的 limit 參數吃這個值，
    # 候選不足時自然回少於此數（甚至 0），這裡只設「上限」不是「目標」。
    channel_daily_max_slots: int = 4
    # 候選主題可被排程的最低分數門檻。選題 LLM 打分低於此值的主題永遠留在
    # candidate 狀態陪跑，不會被 pick_daily_topics 選中，也不算數量不足的錯誤。
    channel_min_topic_score: float = 0.6
    # 每頻道選題庫的目標存量：達標就跳過本輪選題，不為了「湊庫存」白燒 LLM
    # token（見 count_candidates）。5 大約是「兩週份」的緩衝（多數頻道
    # target_interval_days 落在 2~7 天）。
    channel_backlog_target: int = 5
    # 封面上傳大小上限（bytes）。2MB 足夠一張高解析度封面，同時擋住使用者
    # 誤傳原始相機檔案把 R2 儲存成本養大。
    channel_cover_max_bytes: int = 2 * 1024 * 1024

    # ── 寫稿品質（LangGraph pod 用）────────────────────────
    # 寫稿引擎預設用的 CEFR 等級（pod 會帶進 prompt）
    cefr_level: str = "B1"

    # LLM-as-judge 三軸都要達到的門檻（0-1 per axis）
    quality_threshold: float = 0.6

    # judge 不及格 → 觸發 rewrite 的最大次數（cycle cap）。
    # 實測第二輪重寫從未證明能修好被點名的軸，反而常讓分數更差；
    # 下檔已有 best-draft fallback 兜底，1 次補救機會即可。
    max_rewrite_iterations: int = 1

    # topic_type → 寫稿 tone（tone 寫進 prompt）
    tone_map: dict[str, str] = {
        "news": "curious",
        "evergreen": "playful",
        "skill": "contemplative",
        "product": "debate",
    }

    # ── 真實資料來源（gather_evidence_node 用，PRD 重新設計 §1）───
    # 抓取失敗一律降級成空 sources（不阻斷生成），故這裡沒有「必填」欄位；
    # 沒設 key 的 provider 在 factory 裡直接跳過。
    tavily_api_key: str = ""
    tavily_base_url: str = "https://api.tavily.com"
    # product 入口（使用者自訂主題）預設要時效性內容：帶 Tavily 的 topic="news"
    # 才會依 days 篩最近事件，否則排序偏向常青／科普內容，選不到「這幾天發生的事」。
    # evergreen 入口刻意不吃這個值（見 factory.py），深度知識不該被時間窗卡住。
    tavily_recency_days: int = 7
    gdelt_base_url: str = "https://api.gdeltproject.org/api/v2/doc/doc"
    # 用 MediaWiki Action API（穩定、文件完整）而非 REST summary endpoint，
    # 同一支 URL 同時做 srsearch（找標題）與 prop=extracts（拿內文）。
    wikipedia_base_url: str = "https://en.wikipedia.org/w/api.php"
    # Wikimedia API 政策要求可識別的 User-Agent（含聯絡方式），見
    # https://foundation.wikimedia.org/wiki/Policy:Wikimedia_Foundation_User-Agent_Policy
    wikipedia_user_agent: str = "DawnCast/1.0 (https://dawncast.app; contact: ops@dawncast.app)"
    source_fetch_timeout: float = 30.0
    source_max_snippets: int = 5

    # ── Web Push（VAPID）─────────────────────────────────────────
    # 兩個 key 皆為 base64url（applicationServerKey 格式）。留空＝通知功能靜默
    # 關閉（shared/push.py 的第一道 early return），不進 assert_secure：通知是
    # 加值功能，缺 key 不該讓 prod 起不來，也讓 mock pipeline / 測試天然不碰 push。
    vapid_public_key: str = ""
    vapid_private_key: str = ""
    vapid_subject: str = "mailto:ops@dawncast.app"

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
        if not self.supabase_jwks_url and self.supabase_jwt_alg != "HS256":
            raise ConfigError("prod 未設定 SUPABASE_JWKS_URL（不可為空）")
        if self.supabase_jwt_alg == "HS256" and self.supabase_jwt_secret == _DEFAULT_JWT_SECRET:
            raise ConfigError(
                "prod 用 HS256 但 SUPABASE_JWT_SECRET 仍是預設哨兵值（dev-secret-change-me）"
            )
        # HS256 是對稱簽章，secret 太短等於可猜——一旦猜到就能偽造任意 JWT
        # 含 email claim 繞過 admin 白名單。要求 ≥32 chars 對齊 HS256 256-bit
        # entropy 下界。prod 現值 36 char 通過。
        if self.supabase_jwt_alg == "HS256" and len(self.supabase_jwt_secret) < 32:
            raise ConfigError("prod HS256 SUPABASE_JWT_SECRET 必須 ≥32 chars")
        if "*" in self.cors_allowed_origins:
            raise ConfigError("prod 的 CORS_ALLOWED_ORIGINS 不可包含 '*'")
        if self.cors_allowed_origin_regex.strip():
            raise ConfigError("prod 的 CORS_ALLOWED_ORIGIN_REGEX 不可設定（dev-only；prod 留空）")
        if not self.admin_email:
            raise ConfigError("prod 未設定 ADMIN_EMAIL（唯一授權路徑；不可為空）")


@lru_cache
def get_settings() -> Settings:
    return Settings()
