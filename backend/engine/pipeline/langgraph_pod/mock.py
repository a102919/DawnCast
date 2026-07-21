"""LangGraph Pod 的 in-memory infra mock。

啟用條件：環境變數 MOCK_INFRA=1 或缺少 DATABASE_URL。
生產路徑完全不會 import 這些 class——透過 nodes.py 的 lazy import 切換。

設計：
  - MockRepo 對應 shared.db.repo 的函式 surface（只放 pod 用到的部分）。
  - MockR2 對應 shared.storage.r2.put_object 的失敗/成功語意。
  - 所有 mock state 收在 module-level singleton（demo 單 process 可接受，
    pytest 每個 test 自己 reset）。
"""

from __future__ import annotations

import contextlib
import json
import shutil
import tempfile
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from shared.errors import StorageError

# ── MockRepo ────────────────────────────────────────────────


@dataclass
class _EpisodeRow:
    id: str
    slug: str
    idempotency_key: str
    title: str
    topic: str
    big_topic: str
    angle: str
    topic_type: str
    cefr_level: str
    title_zh: str | None = None
    cluster_id: str | None = None
    length_tier: str = "medium"
    format: str = "dialogue"
    grounded: bool = False
    input_tokens: int = 0
    output_tokens: int = 0
    audio_key: str | None = None
    srt_key: str | None = None
    script_json: dict[str, Any] | None = None
    cues: list[dict[str, Any]] | None = None
    extracted_facts: list[dict[str, Any]] | None = None
    target_vocab: list[dict[str, Any]] | None = None


@dataclass
class _DeliveryRow:
    user_id: str
    episode_id: str
    deliver_date: str


@dataclass
class MockRepo:
    """in-memory repo；pod 用的 surface 完整對應 shared.db.repo。

    next_episode_id() 用來 mock DB 的 uuid 生成，避免 race condition。
    """

    episodes: dict[str, _EpisodeRow] = field(default_factory=dict)
    by_idem: dict[str, str] = field(default_factory=dict)  # idempotency_key → episode_id
    deliveries: list[_DeliveryRow] = field(default_factory=list)
    fail_upsert: bool = False  # test hook：模擬 DB 失敗

    def reset(self) -> None:
        self.episodes.clear()
        self.by_idem.clear()
        self.deliveries.clear()
        self.fail_upsert = False

    async def upsert_episode(
        self,
        *,
        idempotency_key: str,
        slug: str,
        title: str,
        topic: str,
        big_topic: str,
        angle: str,
        topic_type: str,
        cefr_level: str = "B1",
        title_zh: str | None = None,
        cluster_id: str | None = None,
        length_tier: str = "medium",
        format: str = "dialogue",
        grounded: bool = False,
        input_tokens: int = 0,
        output_tokens: int = 0,
    ) -> tuple[str, bool]:
        if self.fail_upsert:
            raise RuntimeError("mock: upsert_episode forced failure")
        existing_id = self.by_idem.get(idempotency_key)
        if existing_id is not None:
            return existing_id, True  # already rendered for mock 簡化
        eid = uuid.uuid4().hex[:12]
        self.episodes[eid] = _EpisodeRow(
            id=eid,
            slug=slug,
            idempotency_key=idempotency_key,
            title=title,
            topic=topic,
            big_topic=big_topic,
            angle=angle,
            topic_type=topic_type,
            cefr_level=cefr_level,
            title_zh=title_zh,
            cluster_id=cluster_id,
            length_tier=length_tier,
            format=format,
            grounded=grounded,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
        self.by_idem[idempotency_key] = eid
        return eid, False

    async def update_episode_keys(
        self,
        episode_id: str,
        *,
        audio_key: str | None,
        srt_key: str | None,
        script_json: dict[str, Any],
        cues: list[Any],
        extracted_facts: list[dict[str, Any]] | None = None,
        target_vocab: list[dict[str, Any]] | None = None,
    ) -> None:
        row = self.episodes.get(episode_id)
        if row is None:
            raise RuntimeError(f"mock: episode {episode_id} not found")
        row.audio_key = audio_key
        row.srt_key = srt_key
        row.script_json = script_json
        row.cues = [c.model_dump() if hasattr(c, "model_dump") else c for c in cues]
        row.extracted_facts = extracted_facts
        row.target_vocab = target_vocab

    async def insert_delivery(self, user_id: str, episode_id: str, deliver_date: str) -> bool:
        # 模擬 ON CONFLICT DO NOTHING
        for d in self.deliveries:
            same_key = (
                d.user_id == user_id
                and d.episode_id == episode_id
                and d.deliver_date == deliver_date
            )
            if same_key:
                return False
        self.deliveries.append(_DeliveryRow(user_id, episode_id, deliver_date))
        return True

    async def delete_episode_by_idem(self, idempotency_key: str) -> int:
        """補償用：刪除 audio_key 還 NULL 的 row（鏡像 real repo 的 WHERE 條件）。

        真實 repo 用 `where idempotency_key = %s and audio_r2_key is null`，
        mock 端用 `audio_key is None` 表達同一語意。
        """
        eid = self.by_idem.get(idempotency_key)
        if eid is None or self.episodes.get(eid) is None:
            return 0
        row = self.episodes[eid]
        if row.audio_key is not None:
            return 0  # 已渲染完成的 row 不砍
        del self.episodes[eid]
        del self.by_idem[idempotency_key]
        return 1

    def get_episode(self, episode_id: str) -> _EpisodeRow | None:
        return self.episodes.get(episode_id)


# ── MockR2 ─────────────────────────────────────────────────


@dataclass
class _R2Object:
    key: str
    data: bytes
    content_type: str


@dataclass
class MockR2:
    objects: dict[str, _R2Object] = field(default_factory=dict)
    fail_put: bool = False  # test hook：模擬 StorageError
    fail_keys: set[str] = field(default_factory=set)  # 只對這些 key 失敗

    def reset(self) -> None:
        self.objects.clear()
        self.fail_put = False
        self.fail_keys.clear()

    def put_object(self, key: str, data: bytes, content_type: str) -> None:
        if self.fail_put or key in self.fail_keys:
            raise StorageError(f"mock: put_object {key} forced failure")
        self.objects[key] = _R2Object(key=key, data=data, content_type=content_type)

    def presigned_get_url(self, key: str, ttl: int | None = None) -> str:
        return f"https://mock-r2.local/{key}?ttl={ttl or 7200}"


# ── Mock queue（dict_translate 後處理用，可選）────────────────


@dataclass
class _QueueMsg:
    body: dict[str, Any]


@dataclass
class MockQueue:
    sent: dict[str, list[_QueueMsg]] = field(default_factory=lambda: defaultdict(list))

    def reset(self) -> None:
        self.sent.clear()

    async def send(self, queue_name: str, body: dict[str, Any]) -> int:
        msgs = self.sent[queue_name]
        msgs.append(_QueueMsg(body=body))
        return len(msgs)


# ── Mock render helper（pod demo 用，不真實跑 ffmpeg）──────


@dataclass
class MockRenderer:
    """模擬 render_episode：產空白 mp3 placeholder + cues 從 script 計算。

    用 tempfile 寫實際檔案讓 mp3_path 真實存在（pod 寫到 workdir）。
    """

    workdir: Path

    def render(self, script_payload: dict[str, Any]) -> tuple[Path, str, list[dict[str, Any]]]:
        self.workdir.mkdir(parents=True, exist_ok=True)
        mp3 = self.workdir / "episode.mp3"
        mp3.write_bytes(b"\x00" * 64)  # mock：64 bytes
        cues: list[dict[str, Any]] = []
        t = 0.0
        pause = 0.3
        line_dur = 3.5  # mock 平均行長
        for i, line in enumerate(script_payload.get("script", [])):
            cues.append(
                {
                    "index": i,
                    "speaker": line["speaker"],
                    "text": line["text"],
                    "zh": line["zh"],
                    "start": t,
                    "end": t + line_dur,
                }
            )
            t += line_dur + pause
        srt = self._to_srt(cues)
        return mp3, srt, cues

    @staticmethod
    def _to_srt(cues: list[dict[str, Any]]) -> str:
        out: list[str] = []
        for c in cues:
            start = MockRenderer._fmt_ts(c["start"])
            end = MockRenderer._fmt_ts(c["end"])
            out.append(f"{c['index'] + 1}\n{start} --> {end}\n{c['text']}\n")
        return "\n".join(out)

    @staticmethod
    def _fmt_ts(t: float) -> str:
        h, m, s = int(t // 3600), int(t % 3600 // 60), t % 60
        return f"{h:02d}:{m:02d}:{s:06.3f}"


# ── singleton（demo 單 process）─────────────────────────────

_singleton_repo = MockRepo()
_singleton_r2 = MockR2()
_singleton_queue = MockQueue()


def get_mocks(reset: bool = False) -> tuple[MockRepo, MockR2, MockQueue]:
    if reset:
        _singleton_repo.reset()
        _singleton_r2.reset()
        _singleton_queue.reset()
    return _singleton_repo, _singleton_r2, _singleton_queue


def make_mock_workdir() -> Path:
    return Path(tempfile.mkdtemp(prefix="pod-mock-"))


def safe_local_fallback(mp3_src: Path, slug: str, local_media_dir: str) -> None:
    """對應 production _upload_artifacts 的本地 fallback 邏輯。"""
    if not local_media_dir:
        return
    target = Path(local_media_dir)
    if not target.is_dir():
        return
    # production 也是 warn-and-continue；用 suppress 取代 try/except/pass
    with contextlib.suppress(OSError):
        shutil.copy2(mp3_src, target / f"{slug}.mp3")


# ── local preview dump（demo 印出最終 json）───────────────


def dump_pod_state(state: dict[str, Any], path: Path) -> None:
    """把最終 state 序列化到 json file，方便 demo / debugging。"""
    serializable: dict[str, Any] = {}
    for k, v in state.items():
        if hasattr(v, "model_dump"):
            serializable[k] = v.model_dump()
        elif isinstance(v, Path):
            serializable[k] = str(v)
        else:
            try:
                json.dumps(v)
                serializable[k] = v
            except (TypeError, ValueError):
                serializable[k] = str(v)
    path.write_text(json.dumps(serializable, ensure_ascii=False, indent=2), encoding="utf-8")
