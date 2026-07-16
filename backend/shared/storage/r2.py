"""Cloudflare R2（S3 相容）client。bucket 不公開，對外只發簽章 URL。

金鑰一律從 settings 取（禁硬寫）；失敗 raise StorageError（不洩漏內部細節）。
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any

import boto3  # type: ignore[import-untyped]  # boto3 無 py.typed
from botocore.config import Config  # type: ignore[import-untyped]
from botocore.exceptions import BotoCoreError, ClientError  # type: ignore[import-untyped]

from shared.config import get_settings
from shared.errors import StorageError

logger = logging.getLogger(__name__)


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


def presigned_get_url(key: str, ttl: int | None = None) -> str:
    """產生限時可讀的簽章 URL（預設 settings.r2_signed_url_ttl）。"""
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
