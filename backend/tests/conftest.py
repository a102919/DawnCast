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

# 把 VAPID 清空讓 shared.push.notify_user 在測試裡走 early-return 不打 DB。
# 不想讓 pipeline / reuse 的測試跟 push 訂閱表的存在與否綁在一起——這些測試不
# 驗通知行為。test_notifications.py 自己 monkeypatch get_settings 提供 VAPID，
# 不受這個全域清空影響。
os.environ["VAPID_PRIVATE_KEY"] = ""
os.environ["VAPID_PUBLIC_KEY"] = ""
os.environ["VAPID_SUBJECT"] = ""
