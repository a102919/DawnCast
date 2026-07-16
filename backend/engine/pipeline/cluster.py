"""向量聚類骨架：union-find + cosine 連通分量（V2 才接主流程）。

⚠️ V2 啟用：MVP 重用只用大方向分桶（big_topic 字面），不跑向量聚類（PRD §5 延後）。
本檔提供純函式版的連通分量計算，門檻從 config 取（cluster_threshold_*），
方便 V2 直接接進 orchestrate：把同夜 topic_requests 的向量丟進 connected_components，
同分量視為同一題、共用一集（一次生成多人重用）。

純函式、可獨立測試，不碰 DB / 不打外部。
"""

from __future__ import annotations

import math


class _UnionFind:
    """路徑壓縮 + 按秩合併的 union-find。把「特殊的孤點」和「群組」統一處理。"""

    def __init__(self, n: int) -> None:
        self._parent = list(range(n))
        self._rank = [0] * n

    def find(self, x: int) -> int:
        root = x
        while self._parent[root] != root:
            root = self._parent[root]
        # 路徑壓縮
        while self._parent[x] != root:
            self._parent[x], x = root, self._parent[x]
        return root

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return
        if self._rank[ra] < self._rank[rb]:
            ra, rb = rb, ra
        self._parent[rb] = ra
        if self._rank[ra] == self._rank[rb]:
            self._rank[ra] += 1


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """兩向量的 cosine 相似度。零向量回 0（避免除零特例外溢）。"""
    if len(a) != len(b):
        raise ValueError("向量維度不一致")
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def connected_components(vectors: list[list[float]], threshold: float) -> list[list[int]]:
    """相似度 >= threshold 的向量視為連通，回傳各連通分量的索引清單。

    ⚠️ V2 啟用。O(n²) 兩兩比對——MVP 資料量小、向量重用又延後，先求對；
    上線量大時再換 ANN / ivfflat。
    """
    n = len(vectors)
    uf = _UnionFind(n)
    for i in range(n):
        for j in range(i + 1, n):
            if cosine_similarity(vectors[i], vectors[j]) >= threshold:
                uf.union(i, j)
    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(uf.find(i), []).append(i)
    return list(groups.values())
