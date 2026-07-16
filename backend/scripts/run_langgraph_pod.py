"""LangGraph pod 端到端 demo CLI。

展示跑一集 podcast pipeline 的完整 LangGraph 流程：寫稿 → judge → 渲染 → 上傳 → 交付。
預設走 in-memory mock（`--mock` flag），不需要真 DB / R2 / LLM API key。

用法：
    uv run python -m backend.scripts.run_langgraph_pod --topic "量子力學" --mock
    uv run python -m backend.scripts.run_langgraph_pod --topic "AI 倫理" --angle "常見誤解" --mock
    uv run python -m backend.scripts.run_langgraph_pod --topic "..."  # 走 production
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import date
from pathlib import Path

# 確保 backend/ 在 path 上（`python -m backend.scripts.run_langgraph_pod` 用得到）
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="跑一集 podcast 透過 LangGraph pod（demo 工具）",
    )
    p.add_argument("--topic", required=True, help="題目（中文 / 英文皆可）")
    p.add_argument(
        "--angle",
        default="定義",
        choices=["定義", "人物故事", "常見誤解", "應用場景", "歷史", "對比"],
        help="切入角度（預設：定義）",
    )
    p.add_argument(
        "--mock",
        action="store_true",
        default=True,
        help="走 in-memory mock（demo 預設，無需 DB / R2 / LLM key）",
    )
    p.add_argument(
        "--no-mock",
        dest="mock",
        action="store_false",
        help="走 production（要 DB / R2 / LLM key；無對應 env 會 fail）",
    )
    p.add_argument("--user-ids", nargs="*", default=[], help="收件人 user_ids")
    return p


def make_fake_script(topic: str, angle: str) -> str:
    """產一份固定（合理）的 ScriptJSON JSON 字串，讓 FakeChatModel 吐。"""
    return json.dumps(
        {
            "topic": topic,
            "extracted_facts": [
                {"claim": f"Fact 1 about {topic}", "source_ids": []},
                {"claim": f"Fact 2 about {topic}", "source_ids": []},
                {"claim": f"Fact 3 about {topic}", "source_ids": []},
            ],
            "target_vocab": [
                {"word": "epiphany", "explanation": "a sudden realization"},
                {"word": "paradigm", "explanation": "a typical example or pattern"},
                {"word": "nuance", "explanation": "a small but important detail"},
            ],
            "script": [
                {
                    "speaker": "Alex",
                    "text": f"Welcome to DawnCast. Today we're diving into {topic}.",
                    "zh": f"歡迎來到 DawnCast。今天我們來談 {topic}。",
                },
                {
                    "speaker": "Sarah",
                    "text": f"And the angle is {angle}, which is a fun one.",
                    "zh": f"切入角度是{angle}，這角度挺有趣的。",
                },
                {
                    "speaker": "Alex",
                    "text": "Our first word is epiphany. A sudden realization.",
                    "zh": "第一個字是 epiphany，意思是「頓悟」。",
                },
                {
                    "speaker": "Sarah",
                    "text": "Like when you suddenly get why a friend is upset.",
                    "zh": "就像你突然搞懂朋友為什麼在不爽。",
                },
                {
                    "speaker": "Alex",
                    "text": "Exactly. Then we have paradigm — a typical example.",
                    "zh": "沒錯。再來是 paradigm，意思是「典型範例」。",
                },
                {
                    "speaker": "Sarah",
                    "text": "And nuance, the small but important details.",
                    "zh": "還有 nuance，指那些小但重要的細節。",
                },
                {
                    "speaker": "Alex",
                    "text": "Now, the body. Most people think this is simple, "
                    "but the nuance matters.",
                    "zh": "接下來講重點。大部分人以為這很簡單，但魔鬼藏在細節裡。",
                },
                {
                    "speaker": "Sarah",
                    "text": "Right. Paradigm shifts in this field have happened three times.",
                    "zh": "對。這領域已經歷三次典範轉移。",
                },
                {
                    "speaker": "Alex",
                    "text": "And each epiphany pushed the field forward.",
                    "zh": "而每次頓悟都推進了領域。",
                },
                {
                    "speaker": "Sarah",
                    "text": "So what's the takeaway? Don't skip the nuances.",
                    "zh": "所以重點是什麼？別忽略細節。",
                },
                {
                    "speaker": "Alex",
                    "text": "Exactly. Quick review: epiphany, paradigm, nuance.",
                    "zh": "完全正確。快速複習：頓悟、典範、細節。",
                },
                {
                    "speaker": "Sarah",
                    "text": "Thanks for listening. See you tomorrow.",
                    "zh": "感謝收聽，明天見。",
                },
            ],
        }
    )


def make_fake_judge_passing() -> str:
    return json.dumps(
        {
            "hook_strength": 0.82,
            "informativeness": 0.8,
            "pacing": 0.78,
            "chemistry": 0.75,
            "groundedness": 1.0,
            "feedback": [],
        }
    )


def make_fake_judge_rewrite() -> str:
    return json.dumps(
        {
            "hook_strength": 0.3,
            "informativeness": 0.4,
            "pacing": 0.4,
            "chemistry": 0.5,
            "groundedness": 1.0,
            "feedback": [
                "Add a concrete hook in the intro — start with a scene, not 'welcome'.",
                "Have Alex and Sarah push back on each other at least twice.",
            ],
        }
    )


async def run_demo(args: argparse.Namespace) -> int:
    """跑 demo，印出 episode_id + 路徑。"""
    if not args.mock:
        print(
            "ERROR: --no-mock 需要真 DB / R2 / LLM key；demo 模式請保留 --mock。",
            file=sys.stderr,
        )
        return 1

    from engine.pipeline.langgraph_pod import run_pod
    from engine.pipeline.langgraph_pod.chat import FakeChatModel
    from engine.pipeline.langgraph_pod.mock import (
        MockRenderer,
        get_mocks,
        make_mock_workdir,
    )

    body = {
        "big_topic": args.topic,
        "canonical_topic": args.topic,
        "angle": args.angle,
        "topic_type": "evergreen",
        "deliver_date": date.today().isoformat(),
        "user_ids": args.user_ids,
    }

    # writer：第一次吐中等稿（觸發 judge rewrite），第二次吐升級稿（過 judge）
    mediocre = make_fake_script(args.topic, args.angle).replace(
        "Welcome to DawnCast. Today we're diving into",
        "Today we'll discuss",
    )
    polished = make_fake_script(args.topic, args.angle)

    chat = FakeChatModel(
        responses=[mediocre, polished],
        judge_responses=[make_fake_judge_rewrite(), make_fake_judge_passing()],
    )

    repo, r2, queue = get_mocks(reset=True)
    renderer = MockRenderer(make_mock_workdir())

    print("=" * 60)
    print("DawnCast LangGraph pod demo")
    print(f"  topic    : {args.topic}")
    print(f"  angle    : {args.angle}")
    print("  mock     : True (no DB/R2/LLM key needed)")
    print(f"  user_ids : {args.user_ids or '(none)'}")
    print("=" * 60)
    print()

    print("[1/3] 跑 LangGraph StateGraph（含 judge → rewrite 迴圈）...")
    try:
        episode_id = await run_pod(
            body,
            chat=chat,
            chat_failover=None,
            repo=repo,
            r2=r2,
            queue=queue,
            renderer=renderer,
        )
    except Exception as exc:
        print(f"  FAIL: {exc}", file=sys.stderr)
        return 2
    print(f"  → episode_id = {episode_id}")
    print()

    print("[2/3] DB side effects:")
    ep = repo.get_episode(episode_id)
    if ep is not None:
        line = (
            f"  episode row  : topic={ep.topic!r}  big_topic={ep.big_topic!r}  angle={ep.angle!r}"
        )
        print(line)
        print(f"  audio_key    : {ep.audio_key!r}")
        print(f"  mp4_key      : {ep.mp4_key!r}")
        print(f"  script lines : {len(ep.script_json['script'])}")
    print(f"  deliveries   : {len(repo.deliveries)}")
    print()

    print("[3/3] R2 / queue side effects:")
    print(f"  R2 objects   : {len(r2.objects)}")
    for k, obj in r2.objects.items():
        print(f"    {k:50s} {obj.content_type:25s} {len(obj.data):>8} bytes")
    print(f"  dict_translate msgs : {sum(len(v) for v in queue.sent.values())}")
    print()

    # 展示 judge/rewrite 真的跑了
    print("judge → rewrite cycle:")
    print(f"  writer 呼叫次數 : {chat._writer_count}")
    print(f"  judge  呼叫次數 : {chat._judge_count}")
    print(f"  chat 總呼叫次數 : {chat._call_count}（writer + judge 累計）")
    print(f"  rewrite_iterations cap = {2}（可在 Settings.max_rewrite_iterations 調）")
    print()

    # 印 final state dump
    print("=" * 60)
    print("LangGraph features used:")
    print("  - StateGraph + TypedDict state")
    print("  - add_conditional_edges (rate-limit / already-rendered / judge)")
    print("  - per-node RetryPolicy (GenerationError x3)")
    print("  - checkpointer (MemorySaver, demo 用)")
    print("  - 自訂 BaseChatModel (MiniMax / Fake)")
    print("=" * 60)

    return 0


def main() -> int:
    parser = build_argparser()
    args = parser.parse_args()
    return asyncio.run(run_demo(args))


if __name__ == "__main__":
    raise SystemExit(main())
