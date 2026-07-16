#!/bin/bash
# 診斷字幕漂移問題 — 唯讀，不修改任何檔案
cd /Users/alan/Desktop/code/DawnCast/output/loop_engineering
echo "=== 每段 line mp3 的 container duration (ffprobe) ==="
for f in line_*.mp3; do
  c=$(ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 "$f")
  d=$(ffmpeg -i "$f" -f null - 2>&1 | grep -oE "time=[0-9:.]+" | tail -1)
  printf "  %-30s  container=%ss  decoded=%s\n" "$f" "$c" "$d"
done
echo ""
echo "=== episode.mp3 總長 ==="
ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 episode.mp3
echo ""
echo "=== 字幕時間軸累加 vs 真實時長 ==="
python3 <<'PY'
import subprocess, re, pathlib
out = pathlib.Path(".")
durs = {}
for p in sorted(out.glob("line_*.mp3")):
    r = subprocess.run(["ffmpeg","-i",str(p),"-f","null","-"],
                       capture_output=True, text=True)
    m = re.search(r"time=(\d+):(\d+):(\d+\.\d+)", r.stderr)
    if m:
        h,mn,s = m.groups()
        durs[p.name] = int(h)*3600 + int(mn)*60 + float(s)
total = sum(durs.values()) + 0.3 * (len(durs)-1)
real = float(subprocess.run(
    ["ffprobe","-v","error","-show_entries","format=duration",
     "-of","default=noprint_wrappers=1:nokey=1","episode.mp3"],
    capture_output=True, text=True).stdout.strip())
print(f"  累加預期: {total:.3f}s")
print(f"  實際時長: {real:.3f}s")
print(f"  漂移:     {real-total:+.3f}s")
PY
