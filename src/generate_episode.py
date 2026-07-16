"""Generate a DawnCast podcast episode from a script JSON.

Usage:
    python src/generate_episode.py scripts/loop_engineering.json

Outputs:
    output/<slug>/line_NNN_<Speaker>.mp3
    output/<slug>/episode.mp3   (concatenated final episode)
"""
import asyncio
import json
import pathlib
import sys

import edge_tts

VOICES = {
    "Alex":  "en-US-GuyNeural",
    "Sarah": "en-US-JennyNeural",
}

# 300 ms of silence inserted between speakers for a natural pause
PAUSE_SEC = 0.3
SAMPLE_RATE = 24000


async def synth_line(out_dir: pathlib.Path, i: int, speaker: str, text: str) -> pathlib.Path:
    out = out_dir / f"line_{i:03d}_{speaker}.mp3"
    if out.exists() and out.stat().st_size > 0:
        # Cache hit — skip re-synth
        return out
    comm = edge_tts.Communicate(text, VOICES[speaker])
    await comm.save(str(out))
    return out


async def synth_all(out_dir: pathlib.Path, lines: list[dict]) -> list[pathlib.Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    files = []
    for i, line in enumerate(lines):
        f = await synth_line(out_dir, i, line["speaker"], line["text"])
        print(f"  ✓ {i:03d} {line['speaker']}: {line['text'][:60]}...")
        files.append(f)
    return files


def concat(files: list[pathlib.Path], episode_path: pathlib.Path) -> None:
    """Concatenate mp3 segments with a 300ms silence between speakers.

    We go through wav because ffmpeg's mp3 concat demuxer is unreliable when
    files were encoded by different sources.
    """
    import subprocess

    out_dir = episode_path.parent
    wav_dir = out_dir / "_wav"
    wav_dir.mkdir(exist_ok=True)

    # 1) Generate silence wav
    silence = wav_dir / "silence.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error",
         "-f", "lavfi", "-i", f"anullsrc=r={SAMPLE_RATE}:cl=mono",
         "-t", str(PAUSE_SEC), str(silence)],
        check=True,
    )

    # 2) Convert each mp3 → mono wav at consistent sample rate
    wavs = []
    for mp3 in files:
        wav = wav_dir / (mp3.stem + ".wav")
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error",
             "-i", str(mp3),
             "-ar", str(SAMPLE_RATE), "-ac", "1", str(wav)],
            check=True,
        )
        wavs.append(wav)

    # 3) Build concat list: line1, silence, line2, silence, ...
    # Use absolute paths to avoid ffmpeg CWD-relative resolution issues.
    list_file = out_dir / "concat.txt"
    with list_file.open("w") as f:
        for w in wavs:
            f.write(f"file '{w.resolve()}'\n")
            f.write(f"file '{silence.resolve()}'\n")

    # 4) Concat wavs, then encode final mp3
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error",
         "-f", "concat", "-safe", "0",
         "-i", str(list_file),
         "-c:a", "libmp3lame", "-b:a", "128k",
         str(episode_path)],
        check=True,
    )

    # 5) Clean up wavs to keep output dir tidy
    for w in wavs:
        w.unlink()
    silence.unlink()
    wav_dir.rmdir()


async def main(script_path: str) -> None:
    script = pathlib.Path(script_path)
    data = json.loads(script.read_text())
    slug = script.stem
    out_dir = pathlib.Path("output") / slug

    print(f"→ Topic: {data['topic']}")
    print(f"→ Output: {out_dir}/")
    print(f"→ Synthesizing {len(data['script'])} lines...")

    files = await synth_all(out_dir, data["script"])

    episode = out_dir / "episode.mp3"
    print(f"→ Concatenating → {episode}")
    concat(files, episode)

    # Report duration
    import subprocess
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(episode)],
        capture_output=True, text=True, check=True,
    )
    secs = float(probe.stdout.strip())
    print(f"✓ Done! Duration: {int(secs // 60)}m {int(secs % 60)}s")
    print(f"  File: {episode.resolve()}")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "scripts/loop_engineering.json"))
