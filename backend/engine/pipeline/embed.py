"""嵌入向量計算骨架（V2 才接主流程，PRD §5 向量重用延後）。

MVP 的重用只用「大方向分桶」（big_topic 字面比對，見 reuse.py），不呼叫嵌入。
本檔把 LLM/embedding 介面留好，V2 啟用時只需在 orchestrate 流程串進 embed_topics，
不動其他模組。

⚠️ V2 啟用前：嵌入維度、模型版本與聚類門檻都要真實校準（config 預設值不可拍腦袋上線）。
"""

from __future__ import annotations

import httpx

from shared.config import Settings, get_settings


async def embed_texts(texts: list[str], settings: Settings | None = None) -> list[list[float]]:
    """把多段文字轉成嵌入向量（OpenAI 相容 /embeddings）。

    ⚠️ V2 啟用：MVP 主流程不呼叫此函式。骨架先放好 HTTP 邊界（timeout 在 settings），
    回傳順序對齊輸入順序。
    """
    cfg = settings or get_settings()
    if not texts:
        return []
    timeout = httpx.Timeout(
        connect=cfg.http_connect_timeout,
        read=cfg.http_read_timeout,
        write=cfg.http_read_timeout,
        pool=cfg.http_connect_timeout,
    )
    async with httpx.AsyncClient(
        base_url=cfg.embedding_base_url,
        timeout=timeout,
        headers={"Authorization": f"Bearer {cfg.embedding_api_key}"},
    ) as client:
        resp = await client.post(
            "/embeddings",
            json={
                "model": cfg.embedding_model,
                "input": texts,
                "dimensions": cfg.embedding_dim,
            },
        )
        resp.raise_for_status()
        data = resp.json()
    # 依 index 排序，確保回傳順序對齊輸入
    items = sorted(data["data"], key=lambda d: int(d["index"]))
    return [list(map(float, item["embedding"])) for item in items]
