"""API 契約防漂移：OpenAPI schema 改了但沒人跟著重新產生前端型別時，這裡要炸。

背景（見 tasks/lessons.md「根治撈不到資料反覆故障」）：後端 shared/models.py 是前後端
API 契約的唯一事實來源，frontend/src/api/generated.ts 由它衍生。但如果改了
models.py 卻忘記重跑 `uv run poe export-openapi && npm run gen:api-types`，
compile-time 防護（httpApi.ts 的 satisfies）會繼續盯著一份過期的 generated.ts，
完全抓不到後端已經變了。這支測試用 hash snapshot 頂住這個洞：schema 一變，
這裡就會失敗，逼你把重新產生這個步驟做完再更新 snapshot。
"""

from __future__ import annotations

import hashlib
import json

from app.main import app

# 後端 API 契約變更後：
#   1. cd backend && uv run poe export-openapi
#   2. cd frontend && npm run gen:api-types && npm run typecheck（抓前端沒跟上的地方）
#   3. 把下面這行的 hash 換成新值（跑一次這支測試，錯誤訊息會印出正確值）
_EXPECTED_SCHEMA_HASH = "92298d0c33998c33cf8bb65facd6133c875fa391acb9838cb0e3fad7222c4d2a"


def test_openapi_schema_matches_snapshot() -> None:
    schema = app.openapi()
    actual_hash = hashlib.sha256(json.dumps(schema, sort_keys=True).encode()).hexdigest()
    assert actual_hash == _EXPECTED_SCHEMA_HASH, (
        "OpenAPI schema 變了但 snapshot 沒更新。請先跑：\n"
        "  uv run poe export-openapi\n"
        "  (cd ../frontend && npm run gen:api-types && npm run typecheck)\n"
        "確認前端型別已跟上、httpApi.ts 的 satisfies 沒有紅字，"
        f"再把本檔 _EXPECTED_SCHEMA_HASH 換成 {actual_hash}。"
    )
