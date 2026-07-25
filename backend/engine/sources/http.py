from __future__ import annotations

import asyncio
from typing import TypeVar

import httpx
from pydantic import BaseModel, TypeAdapter, ValidationError

from shared.errors import SourceFetchError

T = TypeVar("T")


async def request_json(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    retries: int = 3,
    params: dict[str, str | int | float | bool | None | list[str]] | None = None,
) -> object:
    last: Exception | None = None
    for attempt in range(retries):
        try:
            response = await client.request(method, url, params=params)
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            last = exc
            if attempt + 1 < retries:
                await asyncio.sleep(0.1 * (2**attempt))
    raise SourceFetchError(f"來源請求失敗：{type(last).__name__}") from last


def validate(data: object, model: type[T]) -> T:  # noqa: UP047
    try:
        return TypeAdapter(model).validate_python(data)
    except ValidationError as exc:
        raise SourceFetchError("來源回應格式無效") from exc


class OpenAlexWork(BaseModel):
    id: str
    title: str | None = None
    doi: str | None = None
    publication_date: str | None = None
    abstract_inverted_index: dict[str, list[int]] | None = None


class OpenAlexResponse(BaseModel):
    results: list[OpenAlexWork] = []


class CrossrefItem(BaseModel):
    DOI: str | None = None
    title: list[str] = []
    URL: str | None = None
    abstract: str | None = None
    published: dict[str, list[int]] | None = None


class CrossrefResponse(BaseModel):
    message: dict[str, object]


class WBItem(BaseModel):
    indicator: dict[str, str] | None = None
    country: dict[str, str] | None = None
    date: str
    value: float | None = None


class FREDResponse(BaseModel):
    observations: list[dict[str, str | None]] = []


class FactResponse(BaseModel):
    claims: list[dict[str, object]] = []


class ArchiveResponse(BaseModel):
    response: dict[str, object]
