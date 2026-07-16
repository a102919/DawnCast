"""claude_code 引擎：用本機 `claude -p` CLI 寫稿，僅供手動跑。

不接夜間批次主流程（factory 預設不選），所以實作從簡：把 prompt 串成一段純文字
丟給 CLI，回應走同一份 parse_engine_result 解析。
"""

from __future__ import annotations

import asyncio
import logging

from shared.config import Settings, get_settings
from shared.errors import EngineError

from .base import EngineResult, GenerationEngine, GenerationRequest
from .prompt import build_messages, parse_engine_result

logger = logging.getLogger(__name__)

_CLI_TIMEOUT = 180.0  # CLI 跑得久，給寬一點


class ClaudeCodeEngine:
    """以 subprocess 呼叫 claude CLI。手動工具，不求完美。"""

    name = "claude_code"

    def __init__(self, settings: Settings | None = None) -> None:
        cfg = settings or get_settings()
        self._model = cfg.api_model  # 只當標記用，CLI 自己決定實際模型

    @staticmethod
    def _flatten_prompt(messages: list[dict[str, str]]) -> str:
        """把 system + user 串成一段純文字 prompt 餵給 CLI。"""
        return "\n\n".join(f"[{m['role'].upper()}]\n{m['content']}" for m in messages)

    async def write_script(self, req: GenerationRequest) -> EngineResult:
        prompt_text = self._flatten_prompt(build_messages(req))
        try:
            proc = await asyncio.create_subprocess_exec(
                "claude",
                "-p",
                prompt_text,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=_CLI_TIMEOUT)
        except FileNotFoundError as exc:
            raise EngineError("找不到 claude CLI，請先安裝 Claude Code") from exc
        except TimeoutError as exc:
            raise EngineError("claude CLI 逾時") from exc

        if proc.returncode != 0:
            # CLI stderr 可能含路徑 / 環境細節，只寫 log；對外訊息保持 generic。
            logger.warning(
                "claude CLI 失敗 exit=%s stderr=%s", proc.returncode, stderr.decode().strip()
            )
            raise EngineError(f"claude CLI 失敗（exit {proc.returncode}）")

        return parse_engine_result(
            stdout.decode(),
            engine=self.name,
            model=self._model,
            usage={},
        )

    async def aclose(self) -> None:
        """無持有資源（每次呼叫起 subprocess），no-op。"""

    async def health(self) -> bool:
        """檢查 claude CLI 是否在 PATH 上。"""
        try:
            proc = await asyncio.create_subprocess_exec(
                "claude",
                "--version",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.communicate(), timeout=10.0)
        except (FileNotFoundError, TimeoutError):
            return False
        return proc.returncode == 0


_: type[GenerationEngine] = ClaudeCodeEngine
