"""LangChain ChatModel adapter：把 DawnCast 的 MiniMax (Anthropic 相容) endpoint
包成 langchain-core BaseChatModel 子類。

兩個實作：
  - MiniMaxChatModel：production 用。打 https://api.minimax.io/anthropic/v1/messages
    ，429 → RateLimitError（failover node 接走），5xx/timeout → 重試後 EngineError。
  - FakeChatModel：mock / 測試用。預載 sequence of responses（script 或拋例外），
    依序吐出；測試可控且決 deterministic。

設計重點：
  * 不掛 with_structured_output（MiniMax 沒原生 tool calling）；
    node 端直接呼叫 ainvoke() 拿 AIMessage，再用既有的 parse_engine_result 解。
  * SecretStr 藏 auth token，metadata 不外洩。
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any, Literal

import httpx
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import ConfigDict, Field, SecretStr

from shared.config import Settings, get_settings
from shared.errors import EngineError, RateLimitError


def _lc_to_anthropic(messages: Sequence[BaseMessage]) -> tuple[str, list[dict[str, str]]]:
    """LangChain messages → Anthropic Messages 格式（system 拆出）。"""
    system_parts: list[str] = []
    conversation: list[dict[str, str]] = []
    for m in messages:
        if isinstance(m, SystemMessage):
            content = m.content if isinstance(m.content, str) else str(m.content)
            system_parts.append(content)
        elif isinstance(m, HumanMessage):
            content = m.content if isinstance(m.content, str) else str(m.content)
            conversation.append({"role": "user", "content": content})
        else:
            # AIMessage / ToolMessage 等：當 user 訊息迴傳，避免 protocol 違規
            content = m.content if isinstance(m.content, str) else str(m.content)
            conversation.append({"role": "assistant", "content": content})
    return "\n\n".join(system_parts), conversation


def _to_usage_metadata(usage: dict[str, Any]) -> dict[str, int] | None:
    """Anthropic usage dict → LangChain UsageMetadata 形狀。空 usage 回 None（無資料非零用量）。"""
    if not usage:
        return None
    input_tokens = int(usage.get("input_tokens") or 0)
    output_tokens = int(usage.get("output_tokens") or 0)
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
    }


class MiniMaxChatModel(BaseChatModel):
    """Production ChatModel：打 MiniMax (Anthropic 相容) /v1/messages。

    429 → RateLimitError（給 graph conditional edge 判斷是否 failover）。
    5xx / timeout → 退避重試 http_max_retries 次後 raise EngineError。
    """

    base_url: str
    auth_token: SecretStr
    model: str
    # 預設 16384：M2.7 reasoning (4k) + 完整 podcast script (12k)。
    # 12288 仍不夠寫 dialogue 腳本（text 被切在 column 12452）。
    max_tokens: int = 16384
    # 顯式給 reasoning 預算避免 LLM 把整個 max_tokens 拿去思考。
    thinking_budget_tokens: int = 4096
    connect_timeout: float = 5.0
    # 180s：MiniMax M3 + extended thinking + 16k max_tokens 在 Zeabur outbound
    # 觀察到單次 response 可達 60-120s（thinking budget 4096 + 12k 腳本）；
    # 原 30s 會讓正常 LLM call 撞 ReadTimeout → EngineError → dead-letter。
    # retry 4 attempts × 180s = 720s 上限，仍然小於 job_timeout_sec=480s × 數倍，
    # 不會掩蓋真實 hang。
    read_timeout: float = 180.0
    max_retries: int = 3

    # 不掛 ctor 副作用（BaseChatModel 透過 pydantic 構造），client lazy 開
    _client: httpx.AsyncClient | None = None

    model_config = ConfigDict(arbitrary_types_allowed=True)

    @property
    def _llm_type(self) -> str:
        return "minimax-anthropic-compat"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        """Sync fallback（langchain 1.4 抽象方法要求）。

        生產路徑只會走 ainvoke → _agenerate；這個方法只在 base 強制要求時存在。
        若真的被同步呼叫，拒絕以避免靜默走錯路徑。
        """
        raise NotImplementedError("MiniMaxChatModel 僅支援 ainvoke；呼叫端請改用 async 介面")

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            token = self.auth_token.get_secret_value()
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=httpx.Timeout(
                    connect=self.connect_timeout,
                    read=self.read_timeout,
                    write=self.read_timeout,
                    pool=self.connect_timeout,
                ),
                headers={
                    "Authorization": f"Bearer {token}",
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _post_with_retry(self, payload: dict[str, Any]) -> dict[str, Any]:
        client = self._get_client()
        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                resp = await client.post("/v1/messages", json=payload)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_exc = exc
                if attempt < self.max_retries:
                    await asyncio.sleep(min(2**attempt, 8) * 0.5)
                    continue
                raise EngineError(f"寫稿引擎連線失敗：{type(exc).__name__}") from exc

            if resp.status_code == 429:
                raise RateLimitError("寫稿引擎撞限流 / 配額（429）")
            if resp.status_code >= 500:
                last_exc = EngineError(f"寫稿引擎回應 {resp.status_code}")
                if attempt < self.max_retries:
                    await asyncio.sleep(min(2**attempt, 8) * 0.5)
                    continue
                raise EngineError(f"寫稿引擎回應 {resp.status_code}")
            if resp.status_code >= 400:
                raise EngineError(f"寫稿引擎回應 {resp.status_code}")
            return resp.json() if resp.content else {}

        raise EngineError("寫稿引擎重試耗盡") from last_exc

    @staticmethod
    def _extract_text(data: dict[str, Any]) -> str:
        content = data.get("content")
        if not isinstance(content, list):
            raise EngineError("寫稿回應缺少 content 陣列")
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
        if not parts:
            raise EngineError("寫稿回應 content 無 text 區塊")
        return "".join(parts)

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,  # CallbackManagerForLLMRun | None
        **kwargs: Any,
    ) -> ChatResult:
        system, conversation = _lc_to_anthropic(messages)
        payload: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": system,
            "messages": conversation,
            # 顯式啟用 extended thinking 並鎖住 reasoning 預算，
            # 否則 LLM 把整個 max_tokens 吃掉就不吐 text 區塊。
            "thinking": {"type": "enabled", "budget_tokens": self.thinking_budget_tokens},
        }
        data = await self._post_with_retry(payload)
        raw_text = self._extract_text(data)
        raw_usage = data.get("usage")
        usage: dict[str, Any] = raw_usage if isinstance(raw_usage, dict) else {}
        return ChatResult(
            generations=[
                ChatGeneration(
                    message=AIMessage(
                        content=raw_text,
                        usage_metadata=_to_usage_metadata(usage),
                    )
                )
            ],
        )


# ── Mock / test ─────────────────────────────────────────────


ChatResponse = str | Exception


class FakeChatModel(BaseChatModel):
    """測試 / demo 用的 ChatModel：依序吐出預載的 response。

    用法：
        chat = FakeChatModel(responses=[
            '{"topic": "...", ...}',                  # 第一次正常
            RateLimitError("mock 429"),               # 第二次模擬限流
            '{"topic": "...", ...}',                  # 第三次（failover 後）
        ])
        text = await chat.ainvoke([SystemMessage(...), HumanMessage(...)])

    也可加 judge_responses 序列，第二次後切到 judge。
    """

    responses: list[ChatResponse] = Field(default_factory=list)
    judge_responses: list[ChatResponse] = Field(default_factory=list)
    role: Literal["writer", "judge"] = "writer"
    _call_count: int = 0
    _writer_count: int = 0
    _judge_count: int = 0

    model_config = ConfigDict(arbitrary_types_allowed=True)

    @property
    def _llm_type(self) -> str:
        return "fake-chat"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        """Sync 版本（langchain 1.4 抽象方法要求）。測試主要走 ainvoke。"""
        resp = self._next()
        self._call_count += 1
        if isinstance(resp, Exception):
            raise resp
        return ChatResult(
            generations=[ChatGeneration(message=AIMessage(content=resp))],
        )

    def _next(self) -> ChatResponse:
        pool = self.judge_responses if self.role == "judge" else self.responses
        if not pool:
            raise RuntimeError(f"FakeChatModel role={self.role} 已無 response 可用")
        # writer / judge 池子各自有獨立 index（切 role 不能共用）
        if self.role == "judge":
            idx = min(self._judge_count, len(pool) - 1)
        else:
            idx = min(self._writer_count, len(pool) - 1)
        return pool[idx]

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        resp = self._next()
        self._call_count += 1
        if self.role == "judge":
            self._judge_count += 1
        else:
            self._writer_count += 1
        if isinstance(resp, Exception):
            raise resp
        return ChatResult(
            generations=[ChatGeneration(message=AIMessage(content=resp))],
        )


def make_langchain_chat(
    settings: Settings | None = None,
    *,
    engine: str | None = None,
) -> MiniMaxChatModel:
    """Factory：從 Settings 組出 production ChatModel。

    engine 覆寫 settings.generation_engine，呼叫端可強制切 api_key（failover）。
    """
    cfg = settings or get_settings()
    chosen = engine or cfg.generation_engine
    if chosen == "minimax":
        return MiniMaxChatModel(
            base_url=cfg.minimax_anthropic_base_url,
            auth_token=SecretStr(cfg.minimax_auth_token),
            model=cfg.minimax_model,
            connect_timeout=cfg.http_connect_timeout,
            read_timeout=cfg.http_read_timeout,
            max_retries=cfg.http_max_retries,
        )
    if chosen == "api_key":
        return MiniMaxChatModel(
            base_url=cfg.api_base_url,
            auth_token=SecretStr(cfg.api_key),
            model=cfg.api_model,
            connect_timeout=cfg.http_connect_timeout,
            read_timeout=cfg.http_read_timeout,
            max_retries=cfg.http_max_retries,
        )
    raise ValueError(f"不支援的 engine={chosen!r}（minimax / api_key only）")
