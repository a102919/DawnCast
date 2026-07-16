"""暫存工作目錄：媒體流程的中間產物（per-line mp3、wav、ass）全寫進這裡。

render 產出的檔案要跨 LangGraph node（render_episode_node → upload_artifacts_node）
存活，所以不能用「離開 with 就自動清掉」的 TemporaryDirectory；改由
upload_artifacts_node 讀完檔案後自行 rmtree（見 nodes.py）。
"""

from __future__ import annotations

import tempfile
from pathlib import Path


def make_job_workdir() -> Path:
    return Path(tempfile.mkdtemp(prefix="dc_media_"))
