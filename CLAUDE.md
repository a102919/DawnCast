# CLAUDE.md（DawnCast 專案）

## 變更後必跑（在全域規則之上，本專案額外規則）

- **改 `backend/shared/models.py`（API 契約 Pydantic model）後**：
  ```
  cd backend && uv run poe export-openapi
  cd frontend && npm run gen:api-types && npm run typecheck
  ```
  `frontend/src/api/generated.ts` 是從後端 OpenAPI schema 衍生的唯一事實來源鏡像
  （見 `backend/tests/test_openapi_contract.py`），忘記重新產生會讓 `httpApi.ts`
  的 `satisfies` 防護盯著過期型別，`uv run poe test` 會被 `test_openapi_contract.py`
  的 hash snapshot 擋下來，錯誤訊息會提示怎麼修。
