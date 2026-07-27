# CLAUDE.md（DawnCast frontend）

## 前端設計

任何 UI 元件、畫面、互動、視覺決策（字型、間距、配色、轉場、手勢）相關的工作，
**開工前必須先用 Skill 工具呼叫 `apple-design`** 取得 Apple 介面設計準則，
再依該準則實作。包含但不限於：

- 新增或調整 React 元件
- 改動 CSS / Tailwind / styled 樣式
- 動畫、轉場、spring / drag / sheet / swipe 互動
- 排版、字級、留白、視覺層級決策
- 無障礙、減少動效偏好

例外：純資料流 / 型別 / API 串接 / 測試邏輯，不涉及視覺或互動，可略過。