"""pytest session 設定：關閉 dev auth bypass，確保授權測試有確定性。

backend/.env 預設會把 DEV_AUTH_BYPASS 開成 true（方便本機手動 curl 不帶 token），
但測試裡 test_no_jwt_*_401 系列預期無 JWT → 401，必須強制關閉 bypass 才能驗證授權路徑。
"""

from __future__ import annotations

import os

# ponytail: 在 get_settings() cache hit 之前就把 env 蓋掉。pydantic-settings 會在
# import 時就讀 env，所以這行必須在所有 app / shared import 之前執行。
os.environ["DEV_AUTH_BYPASS"] = "false"
os.environ["DEV_USER_ID"] = ""
