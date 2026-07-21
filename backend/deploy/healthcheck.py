"""Worker 容器健康檢查。任一項失敗 → exit 非 0，讓 Fly/Docker 重啟容器。

檢查三件事：
  1. DB pool 可連（worker 沒 DB 等於空轉，必檢）。
  2. ffmpeg 在 PATH 且可執行（media 合成的硬依賴，漏裝整條生成都掛）。
  3. 生成引擎憑證有設（可選；只驗設定存在，不打外部 API——失敗只警告不致命）。

整體包硬 timeout，避免 DB 卡住時 health check 永遠不回、Fly 誤判超時。
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys

# 整支檢查的總時限（秒）。比 Dockerfile HEALTHCHECK --timeout=30s 略小，
# 確保是我們自己回 exit code，而不是被外層粗暴 kill。
OVERALL_TIMEOUT = 25.0


async def _check_db() -> None:
    """開一條連線跑 SELECT 1。連不上 / 逾時都算失敗。"""
    from shared.db.pool import close_pool, connection

    try:
        async with connection() as conn, conn.cursor() as cur:
            await cur.execute("SELECT 1")
            await cur.fetchone()
    finally:
        # health check 是短命 process，用完即關，別留著連線。
        await close_pool()


def _check_ffmpeg() -> None:
    """ffmpeg 要在 PATH 且能跑 -version。"""
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg 不在 PATH")
    subprocess.run(
        ["ffmpeg", "-version"],
        check=True,
        capture_output=True,
        timeout=5,
    )


def _check_engine_config() -> bool:
    """寫稿憑證有設即視為健康（不打外部 API，health check 要快且不耗配額）。"""
    from shared.config import get_settings

    cfg = get_settings()
    return bool(cfg.minimax_auth_token or cfg.api_key)


async def _run() -> int:
    # 1. DB（致命）
    await _check_db()

    # 2. ffmpeg（致命）
    _check_ffmpeg()

    # 3. 引擎憑證（非致命：缺憑證時靠 evergreen + 重試兜底，別狂重啟）
    if not _check_engine_config():
        print("warn: 未設定任何寫稿憑證（MINIMAX_AUTH_TOKEN / API_KEY）", file=sys.stderr)

    return 0


def main() -> int:
    try:
        return asyncio.run(asyncio.wait_for(_run(), timeout=OVERALL_TIMEOUT))
    except TimeoutError:
        print(f"error: health check 逾時（>{OVERALL_TIMEOUT}s）", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 — 頂層攔截，任何失敗都回非 0
        print(f"error: health check 失敗：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
