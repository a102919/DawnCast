"""Anthropic 相容 /v1/messages 引擎，以及兩個雲端引擎共用的基底。

ApiKeyEngine 是 fallback（MiniMax 按量 / Anthropic 按量），也是「跑通的基準」。
HTTP + 重試 + 解析邏輯抽進 _AnthropicCompatEngine，minimax.py 直接繼承，
兩者不複製貼上。
"""

from __future__ import annotations

import asyncio

import httpx

from shared.config import Settings, get_settings
from shared.errors import EngineError, GenerationError, RateLimitError

from .base import EngineResult, GenerationEngine, GenerationRequest
from .prompt import build_messages, parse_engine_result

# Anthropic Messages API 要求的版本標頭
_ANTHROPIC_VERSION = "2023-06-01"
_MAX_TOKENS = 4096


class _AnthropicCompatEngine:
    """Anthropic 相容引擎共用實作。子類只需提供 name 與連線參數。

    刻意不直接掛 @runtime_checkable Protocol，而靠 GenerationEngine 結構比對；
    子類實例 isinstance(x, GenerationEngine) 仍成立。
    """

    name: str = "anthropic_compat"

    def __init__(
        self,
        *,
        base_url: str,
        auth_token: str,
        model: str,
        settings: Settings,
    ) -> None:
        self._model = model
        self._auth_token = auth_token  # 不入 log
        self._max_attempts = settings.generation_max_attempts
        self._max_retries = settings.http_max_retries
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx.Timeout(
                connect=settings.http_connect_timeout,
                read=settings.http_read_timeout,
                write=settings.http_read_timeout,
                pool=settings.http_connect_timeout,
            ),
            headers={
                "Authorization": f"Bearer {auth_token}",
                "anthropic-version": _ANTHROPIC_VERSION,
                "content-type": "application/json",
            },
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _post_with_retry(self, payload: dict[str, object]) -> httpx.Response:
        """打 /v1/messages，只重試 5xx / timeout；429 立即 raise RateLimitError。"""
        last_exc: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                resp = await self._client.post("/v1/messages", json=payload)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_exc = exc
                if attempt < self._max_retries:
                    await asyncio.sleep(min(2**attempt, 8) * 0.5)
                    continue
                raise EngineError(f"寫稿引擎連線失敗：{type(exc).__name__}") from exc

            if resp.status_code == 429:
                raise RateLimitError("寫稿引擎撞限流 / 配額（429）")
            if resp.status_code >= 500:
                last_exc = EngineError(f"寫稿引擎回應 {resp.status_code}")
                if attempt < self._max_retries:
                    await asyncio.sleep(min(2**attempt, 8) * 0.5)
                    continue
                raise EngineError(f"寫稿引擎回應 {resp.status_code}")
            if resp.status_code >= 400:
                raise EngineError(f"寫稿引擎回應 {resp.status_code}")
            return resp

        # 理論上不會到這（迴圈內必 return 或 raise），保險用
        raise EngineError("寫稿引擎重試耗盡") from last_exc

    @staticmethod
    def _extract_text(data: dict[str, object]) -> str:
        """從 Anthropic Messages 回應抽出純文字內容。"""
        content = data.get("content")
        if not isinstance(content, list):
            raise GenerationError("寫稿回應缺少 content 陣列")
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
        if not parts:
            raise GenerationError("寫稿回應 content 無 text 區塊")
        return "".join(parts)

    async def _call_once(self, messages: list[dict[str, str]]) -> EngineResult:
        """單次：分離 system / 對話 → post → 抽文字 → 解析成 EngineResult。"""
        system = next((m["content"] for m in messages if m["role"] == "system"), "")
        conversation = [m for m in messages if m["role"] != "system"]
        payload: dict[str, object] = {
            "model": self._model,
            "max_tokens": _MAX_TOKENS,
            "system": system,
            "messages": conversation,
        }
        resp = await self._post_with_retry(payload)
        data = resp.json()
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        raw_text = self._extract_text(data)
        return parse_engine_result(
            raw_text,
            engine=self.name,
            model=self._model,
            usage=usage,
        )

    async def write_script(self, req: GenerationRequest) -> EngineResult:
        """build_messages → 呼叫。GenerationError 時換一次再試，硬上限 generation_max_attempts。"""
        messages = build_messages(req)
        last_err: GenerationError | None = None
        for _ in range(self._max_attempts):
            try:
                return await self._call_once(messages)
            except GenerationError as exc:
                last_err = exc  # LLM 產出不合契約，重生一次
        raise last_err or GenerationError("寫稿重試耗盡仍無合法輸出")

    async def health(self) -> bool:
        """輕量健康檢查：至少要有 auth token 才算可用。"""
        return bool(self._auth_token)


class ApiKeyEngine(_AnthropicCompatEngine):
    """fallback 引擎：用 api_key 打 Anthropic 相容 endpoint（含 MiniMax /anthropic）。"""

    name = "api_key"

    def __init__(self, settings: Settings | None = None) -> None:
        cfg = settings or get_settings()
        super().__init__(
            base_url=cfg.api_base_url,
            auth_token=cfg.api_key,
            model=cfg.api_model,
            settings=cfg,
        )


# 讓 isinstance 與型別檢查確認 ApiKeyEngine 滿足 Protocol
_: type[GenerationEngine] = ApiKeyEngine
