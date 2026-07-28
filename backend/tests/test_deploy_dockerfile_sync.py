"""zeabur-template.yaml 內聯 Dockerfile 防漂移：跟獨立 Dockerfile.api/worker 手動同步，
改一邊忘改另一邊就炸。

背景：Zeabur GIT template schema 的 `dockerfile` 欄位規定必須是內聯內容而非路徑，
所以 backend/deploy/zeabur-template.yaml 裡的 api、worker 兩個 service 各自把
backend/deploy/Dockerfile.api、backend/deploy/Dockerfile.worker 的內容整份貼了一份進去
（本機 docker-compose / 手動 build 仍要用到獨立檔案，兩邊都得留著）。這支測試比照
test_openapi_contract.py 用 hash snapshot 卡衍生檔案漂移的模式，去逐行 diff 內聯內容
與獨立檔案，避免哪天改了 Dockerfile.api 卻忘記同步回 yaml，導致正式部署跟本機悄悄跑出
不同 image。
"""

from __future__ import annotations

import difflib
from pathlib import Path

_DEPLOY_DIR = Path(__file__).resolve().parent.parent / "deploy"
_TEMPLATE_PATH = _DEPLOY_DIR / "zeabur-template.yaml"


def _extract_inline_dockerfile(yaml_lines: list[str], service_name: str) -> list[str]:
    """從 zeabur-template.yaml 抓出指定 service 的 `dockerfile: |` 區塊內容並去除共同縮排。"""
    service_marker = f"    - name: {service_name}"
    start = next(i for i, line in enumerate(yaml_lines) if line == service_marker)
    end = len(yaml_lines)
    for i in range(start + 1, len(yaml_lines)):
        if yaml_lines[i].startswith("    - name: "):
            end = i
            break

    block = yaml_lines[start:end]
    dockerfile_idx = next(i for i, line in enumerate(block) if line.strip() == "dockerfile: |")
    key_indent = len(block[dockerfile_idx]) - len(block[dockerfile_idx].lstrip(" "))

    content: list[str] = []
    for line in block[dockerfile_idx + 1 :]:
        if line.strip() == "":
            content.append("")
            continue
        indent = len(line) - len(line.lstrip(" "))
        if indent <= key_indent:
            break
        content.append(line)

    non_blank = [line for line in content if line.strip()]
    content_indent = min(len(line) - len(line.lstrip(" ")) for line in non_blank)
    return [line[content_indent:] if line.strip() else "" for line in content]


def _normalize(lines: list[str]) -> list[str]:
    """去除註解行、空白行，其餘行去除行尾空白，方便逐行 diff。"""
    return [line.rstrip() for line in lines if line.strip() and not line.strip().startswith("#")]


def _assert_inline_matches_file(service_name: str, dockerfile_path: Path) -> None:
    yaml_lines = _TEMPLATE_PATH.read_text(encoding="utf-8").splitlines()
    inline = _normalize(_extract_inline_dockerfile(yaml_lines, service_name))
    standalone = _normalize(dockerfile_path.read_text(encoding="utf-8").splitlines())

    if inline == standalone:
        return

    diff = "\n".join(
        difflib.unified_diff(
            standalone,
            inline,
            fromfile=str(dockerfile_path),
            tofile=f"zeabur-template.yaml [{service_name}].dockerfile",
            lineterm="",
        )
    )
    raise AssertionError(
        f"zeabur-template.yaml 裡 {service_name} service 的內聯 dockerfile 跟 "
        f"{dockerfile_path.name} 不一致，兩邊需手動同步。\n{diff}"
    )


def test_api_inline_dockerfile_matches_dockerfile_api() -> None:
    _assert_inline_matches_file("api", _DEPLOY_DIR / "Dockerfile.api")


def test_worker_inline_dockerfile_matches_dockerfile_worker() -> None:
    _assert_inline_matches_file("worker", _DEPLOY_DIR / "Dockerfile.worker")
