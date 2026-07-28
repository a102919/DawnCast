"""Cloudflare R2（S3 相容）client。bucket 不公開，對外只發簽章 URL。

金鑰一律從 settings 取（禁硬寫）；失敗 raise StorageError（不洩漏內部細節）。

dev fallback：ENVIRONMENT=dev 且沒填 R2 金鑰時，改讀寫本機檔案（MOCK_R2_DIR，
預設 /tmp/dc_mock_r2）+ 簽出指向 backend /mock-r2/{key} 的 URL（見 app/main.py
的 dev-only 路由）。讓 scripts/generate_one.py --mock 或本機跑 pipeline 時不需要
真 R2 credential 也能讓前端播放。prod（ENVIRONMENT!=dev）永遠走真 R2，不受影響。
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import boto3  # type: ignore[import-untyped]  # boto3 無 py.typed
from botocore.config import Config  # type: ignore[import-untyped]
from botocore.exceptions import BotoCoreError, ClientError  # type: ignore[import-untyped]

from shared.config import get_settings
from shared.errors import StorageError

logger = logging.getLogger(__name__)


def _dev_fallback() -> bool:
    settings = get_settings()
    return settings.environment == "dev" and not settings.r2_access_key_id


def _mock_root() -> Path:
    return Path(os.environ.get("MOCK_R2_DIR", "/tmp/dc_mock_r2"))


@lru_cache
def _client() -> Any:
    settings = get_settings()
    if not settings.r2_endpoint or not settings.r2_access_key_id:
        raise StorageError("R2 未設定")
    return boto3.client(
        "s3",
        endpoint_url=settings.r2_endpoint,
        region_name="auto",
        aws_access_key_id=settings.r2_access_key_id,
        aws_secret_access_key=settings.r2_secret_access_key,
        config=Config(signature_version="s3v4", retries={"max_attempts": 3}),
    )


def put_object(key: str, data: bytes, content_type: str) -> None:
    """上傳物件至私有 bucket。"""
    if _dev_fallback():
        target = _mock_root() / key
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return
    settings = get_settings()
    try:
        _client().put_object(
            Bucket=settings.r2_bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
        )
    except (BotoCoreError, ClientError) as exc:
        logger.error("R2 put_object 失敗 key=%s: %s", key, exc)
        raise StorageError("物件上傳失敗") from exc


def get_object(key: str) -> bytes:
    """下載物件 bytes。給 backfill 從舊 audio_r2_key 撈整集 mp3 用。

    回 bytes 而不是 streaming Response：backfill 一次最多幾 MB，整批進記憶體
    ffmpeg 切段，不需要 streaming 的複雜度。
    """
    if _dev_fallback():
        target = _mock_root() / key
        if not target.is_file():
            raise StorageError("物件不存在（dev mock）")
        return target.read_bytes()
    settings = get_settings()
    try:
        resp = _client().get_object(Bucket=settings.r2_bucket, Key=key)
        return bytes(resp["Body"].read())
    except (BotoCoreError, ClientError) as exc:
        logger.error("R2 get_object 失敗 key=%s: %s", key, exc)
        raise StorageError("物件下載失敗") from exc


def presigned_get_url(key: str, ttl: int | None = None) -> str:
    """產生限時可讀的簽章 URL（預設 settings.r2_signed_url_ttl）。"""
    if _dev_fallback():
        return f"{get_settings().public_base_url.rstrip('/')}/mock-r2/{key}"
    settings = get_settings()
    expires = ttl if ttl is not None else settings.r2_signed_url_ttl
    try:
        url: str = _client().generate_presigned_url(
            "get_object",
            Params={"Bucket": settings.r2_bucket, "Key": key},
            ExpiresIn=expires,
        )
        return url
    except (BotoCoreError, ClientError) as exc:
        logger.error("R2 presign 失敗 key=%s: %s", key, exc)
        raise StorageError("簽章 URL 產生失敗") from exc


def presigned_get_urls(
    keys: list[str], ttl: int | None = None
) -> dict[str, str]:
    """批次簽章多個 R2 key，回傳 {key: signed_url} dict。

    給 Episode GET 把整集 N 個 segments 一次簽完，避免前端 N+1 fetch。
    boto3 generate_presigned_url 本身非同步安全、無 batch API，這裡循序簽
    章（400 段 ≈ 4s，之後若需要再改 concurrent gather）。失敗一筆不擋其他
    鍵：try/except 包住個別簽章，失敗的 key 從回傳 dict 拿掉、上層用空字串
    fallback。空 list 直接回空 dict。
    """
    if _dev_fallback():
        root = _mock_root()
        return {k: presigned_get_url(k, ttl) for k in keys if (root / k).is_file()}
    settings = get_settings()
    expires = ttl if ttl is not None else settings.r2_signed_url_ttl
    client = _client()
    out: dict[str, str] = {}
    for key in keys:
        try:
            out[key] = client.generate_presigned_url(
                "get_object",
                Params={"Bucket": settings.r2_bucket, "Key": key},
                ExpiresIn=expires,
            )
        except (BotoCoreError, ClientError) as exc:
            logger.error("R2 presign 失敗 key=%s: %s", key, exc)
            # 單筆失敗不擋整批，前端會在 decodeAudioData 失敗時走 per-segment error。
    return out
