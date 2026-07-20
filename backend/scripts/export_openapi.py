"""匯出 FastAPI 自動產生的 OpenAPI schema，供前端 openapi-typescript 產生型別用。

app.openapi() 純記憶體操作（app = create_app() 只在 module import 時建構
FastAPI app + 註冊 routers，DB pool 只在 ASGI lifespan 開啟時才連），
不需要真的接資料庫即可離線執行。

執行：
  uv run python -m scripts.export_openapi

單一事實來源：backend/shared/models.py 的 Pydantic model。這支 script 不產生
任何前端型別，只負責把後端契約 dump 成 JSON；前端 `npm run gen:api-types`
再讀這份輸出轉成 TS type。
"""

from __future__ import annotations

import json
from pathlib import Path

from app.main import app

_OUTPUT_PATH = Path(__file__).resolve().parent.parent.parent / "frontend" / "openapi.json"


def export_openapi(output_path: Path = _OUTPUT_PATH) -> Path:
    schema = app.openapi()
    text = json.dumps(schema, indent=2, ensure_ascii=False) + "\n"
    output_path.write_text(text, encoding="utf-8")
    return output_path


if __name__ == "__main__":
    path = export_openapi()
    print(f"已匯出 OpenAPI schema：{path}")
