# 修法 A：整段 TTS 重新生成
# 不用 per-line 拼，改用 SSML <break> 在一個 edge-tts 呼叫中產整集
# 字幕時間軸會跟音訊 1:1 對齊

cd /Users/alan/Desktop/code/DawnCast
source .venv/bin/activate
python3 <<'PY'
import asyncio, json, edge_tts, pathlib

script = json.loads(pathlib.Path("scripts/loop_engineering.json").read_text())
VOICES = {"Alex": "en-US-GuyNeural", "Sarah": "en-US-JennyNeural"}

# Build a single SSML script with 300ms <break> between speakers
ssml_lines = []
for line in script["script"]:
    spk = line["speaker"]
    txt = line["text"]
    # Escape XML special chars
    txt = txt.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
    ssml_lines.append(f'<voice name="{VOICES[spk]}">{txt}</voice>')
    ssml_lines.append('<break time="300ms"/>')
ssml = '<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="en-US">' + "".join(ssml_lines) + "</speak>"

# Save the SSML for inspection
pathlib.Path("output/loop_engineering/full.ssml").write_text(ssml, encoding="utf-8")
print(f"SSML length: {len(ssml)} chars")

# Synthesize
async def main():
    comm = edge_tts.Communicate(ssml, VOICES["Alex"])
    await comm.save("output/loop_engineering/episode_full.mp3")
asyncio.run(main())
print("✓ Saved episode_full.mp3")

# Now cue boundaries = sum of (per-line mp3 durations) + 0.3s
# Regenerate subtitles using the same logic but in lockstep with this single file
PY
echo "DONE. Check episode_full.mp3 then re-run subtitle generator with --single-file mode"
