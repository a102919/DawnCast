"""Generate bilingual (EN + ZH) subtitles and a subtitled video for a DawnCast episode.

Assumes you have already run generate_episode.py and the per-line mp3 segments
exist in output/<slug>/.

Outputs:
    output/<slug>/subtitles.srt      (bilingual SRT, EN over ZH)
    output/<slug>/subtitles.vtt      (bilingual WebVTT)
    output/<slug>/subtitles.json     (structured per-line data for web player)
    output/<slug>/episode.mp4        (video with burned-in subtitles + waveform bg)
"""
import json
import pathlib
import subprocess
import sys
import textwrap


def load_script(slug: str) -> tuple[str, dict[str, str], dict[str, str]]:
    """Load the script JSON and return (topic, en_lines, zh_lines) keyed by line key."""
    src = pathlib.Path("scripts") / f"{slug}.json"
    data = json.loads(src.read_text(encoding="utf-8"))
    en_lines: dict[str, str] = {}
    zh_lines: dict[str, str] = {}
    for i, entry in enumerate(data["script"]):
        key = f"line_{i:03d}_{entry['speaker']}"
        en_lines[key] = entry["text"]
        zh_lines[key] = entry["zh"]
    return data["topic"], en_lines, zh_lines


def measure_segments(out_dir: pathlib.Path) -> list[dict]:
    """Use ffprobe to get the duration of each line mp3."""
    lines = sorted(out_dir.glob("line_*.mp3"))
    segments = []
    for p in lines:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(p)],
            capture_output=True, text=True, check=True,
        )
        segments.append({
            "key":   p.stem,
            "path":  p,
            "dur":   float(probe.stdout.strip()),
        })
    return segments


def build_timeline(segments: list[dict], pause: float = 0.3) -> list[dict]:
    """Assign start/end timestamps to each segment."""
    timeline = []
    cursor = 0.0
    for seg in segments:
        start = cursor
        end = start + seg["dur"]
        timeline.append({**seg, "start": start, "end": end})
        cursor = end + pause
    return timeline


def fmt_ts(seconds: float) -> str:
    """SRT-style HH:MM:SS,mmm"""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int(round((seconds - int(seconds)) * 1000))
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def fmt_vtt_ts(seconds: float) -> str:
    """WebVTT uses . instead of ,"""
    return fmt_ts(seconds).replace(",", ".")


def write_srt(timeline: list[dict], en_lines: dict[str, str], zh_lines: dict[str, str], out: pathlib.Path):
    chunks = []
    for i, seg in enumerate(timeline, 1):
        en = en_lines[seg["key"]]
        zh = zh_lines[seg["key"]]
        chunks.append(
            f"{i}\n"
            f"{fmt_ts(seg['start'])} --> {fmt_ts(seg['end'])}\n"
            f"{en}\n"
            f"{zh}\n"
        )
    out.write_text("\n".join(chunks), encoding="utf-8")


def write_vtt(timeline: list[dict], en_lines: dict[str, str], zh_lines: dict[str, str], out: pathlib.Path):
    # Build the entire VTT in one shot (header + cues) and write once
    chunks = ["WEBVTT", ""]
    for i, seg in enumerate(timeline, 1):
        en = en_lines[seg["key"]]
        zh = zh_lines[seg["key"]]
        chunks.extend([
            f"{i}",
            f"{fmt_vtt_ts(seg['start'])} --> {fmt_vtt_ts(seg['end'])}",
            en,
            zh,
            "",
        ])
    out.write_text("\n".join(chunks), encoding="utf-8")


def write_json(timeline: list[dict], topic: str, en_lines: dict[str, str], zh_lines: dict[str, str], out: pathlib.Path):
    payload = {
        "topic": topic,
        "cues": [
            {
                "index":   i,
                "speaker": seg["key"].rsplit("_", 1)[1],
                "start":   round(seg["start"], 3),
                "end":     round(seg["end"], 3),
                "en":      en_lines[seg["key"]],
                "zh":      zh_lines[seg["key"]],
            }
            for i, seg in enumerate(timeline, 1)
        ],
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def burn_video(timeline: list[dict], en_lines: dict[str, str], zh_lines: dict[str, str], mp3: pathlib.Path, out: pathlib.Path):
    """Render an MP4 with a static gradient background, the audio, and burned bilingual subs.

    Two-line subtitle per cue: English (yellow) above Chinese (white).
    """
    # Build an ASS file with bilingual subs for each cue
    ass_path = out.parent / "_subs.ass"
    ass_header = textwrap.dedent("""\
        [Script Info]
        ScriptType: v4.00+
        PlayResX: 1280
        PlayResY: 720

        [V4+ Styles]
        Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
        Style: En,Helvetica,40,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,2,1,2,60,60,80,1
        Style: Zh,Noto Sans CJK TC,40,&H0033E6FF,&H000000FF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,2,1,2,60,60,30,1

        [Events]
        Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
    """)
    ass_body = []
    for seg in timeline:
        en = en_lines[seg["key"]]
        zh = zh_lines[seg["key"]]
        start = fmt_vtt_ts(seg["start"])
        end = fmt_vtt_ts(seg["end"])
        ass_body.append(
            f"Dialogue: 0,{start},{end},En,,0,0,0,,{en}\n"
            f"Dialogue: 0,{start},{end},Zh,,0,0,0,,{zh}\n"
        )
    ass_path.write_text(ass_header + "".join(ass_body), encoding="utf-8")

    # Get total duration of the audio (used to size the video)
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(mp3)],
        capture_output=True, text=True, check=True,
    )
    dur = float(probe.stdout.strip())

    # Escape colons in ASS path for the ffmpeg filter (Windows-friendly, harmless on macOS)
    ass_escaped = str(ass_path).replace("\\", "/").replace(":", "\\:")

    # Build a 1280x720 dark-blue background with a subtle vignette, then overlay subtitles.
    # The color source is the -i input [0:v]; we just add a vignette + subtitles on top.
    vf = f"vignette=PI/4,subtitles={ass_escaped}:si=0:force_style='Outline=2,Shadow=1'"

    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", f"color=c=0x0F1A2E:s=1280x720:d={dur}:r=24",
            "-i", str(mp3),
            "-vf", vf,
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-c:a", "aac", "-b:a", "128k",
            "-shortest",
            "-pix_fmt", "yuv420p",
            str(out),
        ],
        check=True,
    )
    ass_path.unlink()


def main(episode_dir: str):
    ep_dir = pathlib.Path(episode_dir)
    if not ep_dir.exists():
        sys.exit(f"Missing {ep_dir} — run generate_episode.py first.")

    slug = ep_dir.name
    topic, en_lines, zh_lines = load_script(slug)

    print(f"→ Measuring segments in {ep_dir}/")
    segments = measure_segments(ep_dir)
    timeline = build_timeline(segments)
    print(f"  {len(timeline)} cues, total ~{timeline[-1]['end']:.1f}s")

    print("→ Writing subtitles.srt")
    write_srt(timeline, en_lines, zh_lines, ep_dir / "subtitles.srt")

    print("→ Writing subtitles.vtt")
    write_vtt(timeline, en_lines, zh_lines, ep_dir / "subtitles.vtt")

    print("→ Writing subtitles.json")
    write_json(timeline, topic, en_lines, zh_lines, ep_dir / "subtitles.json")

    print("→ Rendering episode.mp4 with burned-in subtitles")
    burn_video(timeline, en_lines, zh_lines,
               ep_dir / "episode.mp3", ep_dir / "episode.mp4")

    print("✓ Done.")


if __name__ == "__main__":
    slug = sys.argv[1] if len(sys.argv) > 1 else "loop_engineering"
    main(f"output/{slug}")
