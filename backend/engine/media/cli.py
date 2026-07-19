"""薄 CLI：手動跑單集，方便驗證。

用法：
    python -m engine.media.cli ../scripts/loop_engineering.json [out_dir]

把成品（episode.mp3、subtitles.srt/vtt/json）寫到 out_dir，
out_dir 預設為 /tmp/dc_media_<slug>。直接渲染到指定目錄，不經 TemporaryDirectory。
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from shared.models import ScriptJSON

from . import render_episode
from .subtitles import cues_to_json


async def _run(script_path: Path, out_dir: Path) -> None:
    script = ScriptJSON.model_validate_json(script_path.read_text(encoding="utf-8"))
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"→ Topic: {script.topic}")
    print(f"→ Output: {out_dir}/")
    print(f"→ Rendering {len(script.script)} lines...")

    artifacts = await render_episode(script, out_dir)

    (out_dir / "subtitles.srt").write_text(artifacts.srt, encoding="utf-8")
    (out_dir / "subtitles.vtt").write_text(artifacts.vtt, encoding="utf-8")
    (out_dir / "subtitles.json").write_text(
        json.dumps(
            {"topic": script.topic, "cues": cues_to_json(artifacts.cues)},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    total = artifacts.cues[-1].end if artifacts.cues else 0.0
    print(f"✓ Done. {len(artifacts.cues)} cues, ~{total:.1f}s")
    print(f"  mp3: {artifacts.mp3_path}")


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit("用法：python -m engine.media.cli <script.json> [out_dir]")
    script_path = Path(sys.argv[1])
    if not script_path.exists():
        sys.exit(f"找不到 script：{script_path}")
    out_dir = (
        Path(sys.argv[2]) if len(sys.argv) > 2 else Path("/tmp") / f"dc_media_{script_path.stem}"
    )
    asyncio.run(_run(script_path, out_dir))


if __name__ == "__main__":
    main()
