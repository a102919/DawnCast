"""本機測 MiniMax TTS / LLM 真實併發上限（一次性診斷腳本，會消耗真實訂閱額度）。

⚠️ 本機 .env 的 MINIMAX_AUTH_TOKEN 跟 prod 共用同一個訂閱額度池。MiniMax 是
Token Plan 額度制，不是單純每秒限流——撞到「額度打光」任何 retry/降速都沒用，
只能等下個計費週期或去 dashboard 加值（見 memory llm-provider-minimax.md 2026-07-12
案例：連續 3 次完整 generate_one 就把當期額度燒光）。跑這支腳本前先確認能接受
消耗額度、且可能影響同一額度池下還在跑的其他生成任務。

用法（預設 dry-run，只印會發送什麼，不打真實 API）：
    uv run python -m scripts.probe_minimax_concurrency tts --execute
    uv run python -m scripts.probe_minimax_concurrency llm --execute

演算法：concurrency 從 2 開始倍增到封頂 32，每輪用 asyncio.gather 同時發送。
遇到「額度打光」訊號立刻整支腳本中止；遇到其他 429（單純爆發限流）記錄為上限
後停止 ramp；timeout/5xx 視為雜訊記錄但繼續往上測。
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import time
from dataclasses import dataclass, field

import httpx
from langchain_core.messages import HumanMessage

from engine.media.tts import MINIMAX_VOICES, _minimax_tts_request
from engine.pipeline.langgraph_pod.chat import make_langchain_chat
from shared.config import get_settings
from shared.errors import TTSError

logger = logging.getLogger(__name__)

_MAX_CONCURRENCY = 32
_ROUND_COOLDOWN_SEC = 2.0
_QUOTA_EXHAUSTED_MARKER = "Token Plan usage limit reached"

_TTS_TEXT = "Testing concurrency, please ignore."
_LLM_PROMPT = "回覆兩個字：測試完成"


@dataclass
class _RoundResult:
    concurrency: int
    ok: int
    failed: int
    quota_exhausted: bool
    other_429: bool
    latencies: list[float] = field(default_factory=list)


def _classify(exc: BaseException) -> str:
    """回傳 'quota' / 'burst' / 'other'。"""
    msg = str(exc)
    if _QUOTA_EXHAUSTED_MARKER in msg:
        return "quota"
    if "429" in msg or "rate_limit" in msg.lower():
        return "burst"
    return "other"


async def _run_round(concurrency: int, call) -> _RoundResult:  # type: ignore[no-untyped-def]
    async def timed() -> float:
        start = time.monotonic()
        await call()
        return time.monotonic() - start

    results = await asyncio.gather(*(timed() for _ in range(concurrency)), return_exceptions=True)
    ok = [r for r in results if isinstance(r, float)]
    errors = [r for r in results if isinstance(r, BaseException)]
    kinds = {_classify(e) for e in errors}
    for e in errors:
        logger.info("併發=%d 失敗樣本：%s", concurrency, e)
    return _RoundResult(
        concurrency=concurrency,
        ok=len(ok),
        failed=len(errors),
        quota_exhausted="quota" in kinds,
        other_429="burst" in kinds,
        latencies=ok,
    )


async def _ramp(label: str, call, execute: bool) -> None:  # type: ignore[no-untyped-def]
    concurrency = 2
    total_calls = 0
    rounds: list[_RoundResult] = []
    while concurrency <= _MAX_CONCURRENCY:
        if not execute:
            logger.info("[dry-run] %s 併發=%d 會發送 %d 個真實請求", label, concurrency, concurrency)
            concurrency *= 2
            continue

        total_calls += concurrency
        logger.info("%s 開始測併發=%d（累計已發送 %d 次）", label, concurrency, total_calls)
        result = await _run_round(concurrency, call)
        rounds.append(result)

        if result.quota_exhausted:
            logger.error(
                "%s 在併發=%d 撞到額度打光（Token Plan usage limit reached），立刻停止測試",
                label,
                concurrency,
            )
            break
        if result.other_429:
            logger.warning(
                "%s 在併發=%d 出現爆發限流（非額度打光），判定此為上限，停止 ramp",
                label,
                concurrency,
            )
            break

        avg = sum(result.latencies) / len(result.latencies) if result.latencies else 0.0
        logger.info(
            "%s 併發=%d 全數成功（%d/%d），平均耗時 %.2fs",
            label,
            concurrency,
            result.ok,
            concurrency,
            avg,
        )
        concurrency *= 2
        if concurrency <= _MAX_CONCURRENCY:
            await asyncio.sleep(_ROUND_COOLDOWN_SEC)

    if not execute:
        logger.info("[dry-run] %s 結束，未發送任何真實請求", label)
        return

    logger.info("── %s 摘要 ──", label)
    for r in rounds:
        verdict = "QUOTA_EXHAUSTED" if r.quota_exhausted else "BURST_LIMIT" if r.other_429 else "OK"
        logger.info("  併發=%-3d 成功=%-3d 失敗=%-3d 判定=%s", r.concurrency, r.ok, r.failed, verdict)
    if rounds and rounds[-1].quota_exhausted:
        logger.info("結論：%s 於併發=%d 撞到額度打光，需等下個計費週期或加值", label, rounds[-1].concurrency)
    elif rounds and rounds[-1].other_429:
        safe = rounds[-2].concurrency if len(rounds) > 1 else 1
        logger.info("結論：%s 併發上限 ≈ %d（於併發=%d 開始爆發限流）", label, safe, rounds[-1].concurrency)
    elif rounds:
        logger.info("結論：%s 到封頂併發=%d 都沒撞到限制", label, rounds[-1].concurrency)


async def _cmd_tts(execute: bool) -> None:
    settings = get_settings()
    if not settings.minimax_auth_token:
        raise SystemExit("本機 .env 沒設 MINIMAX_AUTH_TOKEN，無法測真實 API")

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(
            connect=settings.http_connect_timeout,
            read=settings.http_read_timeout,
            write=settings.http_read_timeout,
            pool=settings.http_connect_timeout,
        ),
        headers={"Authorization": f"Bearer {settings.minimax_auth_token}"},
    ) as client:

        async def call() -> None:
            payload = {
                "model": settings.minimax_tts_model,
                "text": _TTS_TEXT,
                "voice_setting": {"voice_id": MINIMAX_VOICES["Alex"], "speed": 1.0},
                "audio_setting": {"format": "mp3", "sample_rate": 32000},
                "subtitle_enable": False,
                "stream": False,
            }
            try:
                await _minimax_tts_request(client, settings, payload)
            except TTSError as exc:
                raise RuntimeError(str(exc)) from exc

        await _ramp("TTS", call, execute)


async def _cmd_llm(execute: bool) -> None:
    settings = get_settings()
    if not settings.minimax_auth_token:
        raise SystemExit("本機 .env 沒設 MINIMAX_AUTH_TOKEN，無法測真實 API")
    chat = make_langchain_chat(settings)

    async def call() -> None:
        await chat.ainvoke([HumanMessage(content=_LLM_PROMPT)])

    await _ramp("LLM", call, execute)


def _amain() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp_tts = sub.add_parser("tts", help="測 MiniMax TTS 真實併發上限")
    sp_tts.add_argument("--execute", action="store_true", help="不加就是 dry-run，只印訊息")

    sp_llm = sub.add_parser("llm", help="測 MiniMax LLM 真實併發上限")
    sp_llm.add_argument("--execute", action="store_true", help="不加就是 dry-run，只印訊息")

    args = p.parse_args()
    if args.execute:
        logger.warning(
            "即將消耗真實 MiniMax 訂閱額度（跟 prod 共用同一個池），封頂併發=%d", _MAX_CONCURRENCY
        )

    if args.cmd == "tts":
        asyncio.run(_cmd_tts(args.execute))
    elif args.cmd == "llm":
        asyncio.run(_cmd_llm(args.execute))


if __name__ == "__main__":
    _amain()
