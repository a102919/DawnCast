"""engine.media.dict_audio helper 測試。

方案 B 上線後 /dict/lookup 不再 inline 觸發 synthesize_word_audio（見
app/routers/dict.py），但 helper 本身仍保留 export 給：
  - scripts/backfill_audio.py（DEPRECATED，見該檔 docstring）
  - 任何離線 / 歷史排程

本檔只測 helper 行為：^[a-z]+$ 守門、synthesize / publish 例外降級。

⚠️ 整合測試（_ensure_audio_url 路徑）已移除，因為前端不再用
   dict_cache.audio_url，router 端的觸發邏輯在 Phase G1a 一併拔除。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from engine.media import dict_audio


def _async_fn(sync_fn: Any) -> Any:
    """包裝同步函式成 async coroutine factory（給 monkeypatch setattr 用）。"""

    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        return sync_fn(*args, **kwargs)

    return wrapper


# ── helper 層級守門：^[a-z]+$ 之外的輸入直接 None ────────────────────


def test_synthesize_word_audio_rejects_non_single_word(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dict_audio, "_synthesize", lambda w, model: b"fake")
    monkeypatch.setattr(dict_audio, "_publish", _async_fn(lambda w, data, ct: "https://x/y.wav"))

    assert asyncio.run(dict_audio.synthesize_word_audio("hello world")) is None
    assert asyncio.run(dict_audio.synthesize_word_audio("hello-world")) is None
    assert asyncio.run(dict_audio.synthesize_word_audio("hello123")) is None
    assert asyncio.run(dict_audio.synthesize_word_audio("Hello")) is None  # 大寫不符
    # 合 ^[a-z]+$ → 走 helper 流程
    assert asyncio.run(dict_audio.synthesize_word_audio("hello")) == "https://x/y.wav"


def test_synthesize_word_audio_handles_synth_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """底層 _synthesize 拋例外時，helper 必須降級回 None（不外拋污染路由）。"""

    def _boom(word: str, model: str) -> bytes:
        raise RuntimeError("piper not installed")

    monkeypatch.setattr(dict_audio, "_synthesize", _boom)

    assert asyncio.run(dict_audio.synthesize_word_audio("hello")) is None


def test_synthesize_word_audio_handles_publish_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """_publish 回 None（無 R2 也無本地 dir）時 helper 仍降級回 None。"""

    async def _publish_none(word: str, data: bytes, ct: str) -> str | None:
        return None

    monkeypatch.setattr(dict_audio, "_synthesize", lambda w, model: b"fake")
    monkeypatch.setattr(dict_audio, "_publish", _publish_none)

    assert asyncio.run(dict_audio.synthesize_word_audio("hello")) is None
