"""夜間 pipeline 與 worker 的純邏輯 / mock 測試（不連 DB、不打外部 API）。

驗證重點：
  1. deterministic_normalize：正規化等價類。
  2. reuse anti-join 分支：命中只交付、未命中才 enqueue（用 fake repo/queue）。
  3. generate_job：串接順序、R2 key 格式、failover/degrade 行為（全 mock）。
  4. worker dispatch：control/generate 路由、成功 delete、read_ct>=N archive、超時不 delete。

本機沒有 Supabase/pgmq，所以 DB/佇列/外部一律 monkeypatch 成 in-memory 假件，
只驗 Python 側的決策邏輯。SQL 本身的正確性靠型別 + migration 保證（Phase 5 真連驗）。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from engine.pipeline import generate_job, reuse
from engine.pipeline.cluster import connected_components, cosine_similarity
from engine.pipeline.langgraph_pod.chat import FakeChatModel
from engine.pipeline.langgraph_pod.mock import MockRenderer, make_mock_workdir
from engine.pipeline.normalize import deterministic_normalize
from shared.errors import RateLimitError
from shared.models import Cue, ScriptJSON

# ── 1. deterministic_normalize 等價類 ──────────────────────────────


def test_normalize_equivalence_class() -> None:
    base = deterministic_normalize("quantum computing")
    assert deterministic_normalize("Quantum Computing") == base
    assert deterministic_normalize("  quantum   computing  ") == base
    assert deterministic_normalize("Quantum, Computing!") == base
    # 全形空白 / 標點也收斂
    assert deterministic_normalize("ＱＵＡＮＴＵＭ　ＣＯＭＰＵＴＩＮＧ") == base


def test_normalize_distinguishes_real_difference() -> None:
    assert deterministic_normalize("machine learning") != deterministic_normalize("deep learning")


def test_normalize_preserves_cjk() -> None:
    assert deterministic_normalize("量子 計算") == "量子 計算"


# ── cluster 純函式（V2 骨架，仍要可測）─────────────────────────────


def test_cosine_similarity_basics() -> None:
    assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)
    assert cosine_similarity([0.0, 0.0], [1.0, 1.0]) == 0.0  # 零向量不爆


def test_connected_components_groups_by_threshold() -> None:
    vecs = [[1.0, 0.0], [0.99, 0.01], [0.0, 1.0]]
    comps = connected_components(vecs, threshold=0.9)
    # 前兩個相近成一組，第三個自成一組
    sizes = sorted(len(c) for c in comps)
    assert sizes == [1, 2]


# ── 共用：fake repo / queue ────────────────────────────────────────


class FakeRepo:
    """記錄重用決策呼叫，模擬 find_reusable_episode 命中 / 未命中。

    Phase 4：簽名加 length_tier keyword-only；介面與 production repo 對齊。
    真實判斷式（分 tier）單獨在新測試 test_reuse_distinguishes_by_length_tier 驗證。
    """

    def __init__(self, reusable: str | None) -> None:
        self._reusable = reusable
        # 記下每次查詢收到的 length_tier，方便新測試斷言「介面真的傳到」。
        self.find_calls: list[tuple[str, str, str]] = []
        self.deliveries: list[tuple[str, str, str]] = []

    async def find_reusable_episode(
        self,
        big_topic: str,
        user_id: str,
        *,
        length_tier: str = "medium",
    ) -> str | None:
        self.find_calls.append((big_topic, user_id, length_tier))
        return self._reusable

    async def insert_delivery(self, user_id: str, episode_id: str, deliver_date: str) -> bool:
        self.deliveries.append((user_id, episode_id, deliver_date))
        return True


class FakeQueue:
    def __init__(self) -> None:
        self.sent: list[tuple[str, dict[str, Any]]] = []

    async def send(self, queue: str, body: dict[str, Any]) -> int:
        self.sent.append((queue, body))
        return len(self.sent)


class _TieredFakeRepo(FakeRepo):
    """test_reuse_distinguishes_by_length_tier 用：依 length_tier 決定命中哪集。"""

    def __init__(self, short_id: str, long_id: str) -> None:
        super().__init__(reusable=None)
        self._short_id = short_id
        self._long_id = long_id

    async def find_reusable_episode(
        self,
        big_topic: str,
        user_id: str,
        *,
        length_tier: str = "medium",
    ) -> str | None:
        self.find_calls.append((big_topic, user_id, length_tier))
        if length_tier == "short":
            return self._short_id
        if length_tier == "long":
            return self._long_id
        return None


# ── 2. reuse anti-join 分支 ────────────────────────────────────────


async def test_reuse_hit_only_delivers(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = FakeRepo(reusable="ep-123")
    q = FakeQueue()
    monkeypatch.setattr(reuse, "repo", repo)
    monkeypatch.setattr(reuse, "queue", q)

    result = await reuse.resolve_for_user(user_id="u1", big_topic="ai", deliver_date="2026-06-23")
    assert result == "ep-123"
    assert repo.deliveries == [("u1", "ep-123", "2026-06-23")]
    assert q.sent == []  # 命中不排生成


async def test_reuse_miss_enqueues_generate(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = FakeRepo(reusable=None)
    q = FakeQueue()
    monkeypatch.setattr(reuse, "repo", repo)
    monkeypatch.setattr(reuse, "queue", q)

    result = await reuse.resolve_for_user(
        user_id="u1",
        big_topic="ai",
        deliver_date="2026-06-23",
        angle="人物故事",
        topic_type="news",
        length_tier="short",
    )
    assert result is None
    assert repo.deliveries == []  # 未命中不交付
    assert len(q.sent) == 1
    qname, body = q.sent[0]
    assert qname == "generate"
    assert body["big_topic"] == "ai"
    assert body["angle"] == "人物故事"
    assert body["user_ids"] == ["u1"]
    # Phase 4：topic_type / length_tier 也帶進 generate body。
    assert body["topic_type"] == "news"
    assert body["length_tier"] == "short"


async def test_reuse_distinguishes_by_length_tier(monkeypatch: pytest.MonkeyPatch) -> None:
    """Phase 4：find_reusable_episode 真的拿到 length_tier，並決定命中 vs 未命中。

    用 subclass 讓 FakeRepo 依 tier 決定回傳的 episode：同 big_topic/同 user 但不同
    length_tier 不算命中。確保舊介面「只看 big_topic」的真實漏洞被新介面收緊。
    """
    repo = _TieredFakeRepo(short_id="ep-short", long_id="ep-long")
    q = FakeQueue()
    monkeypatch.setattr(reuse, "repo", repo)
    monkeypatch.setattr(reuse, "queue", q)

    short_hit = await reuse.resolve_for_user(
        user_id="u1",
        big_topic="ai",
        deliver_date="2026-06-23",
        length_tier="short",
    )
    long_hit = await reuse.resolve_for_user(
        user_id="u1",
        big_topic="ai",
        deliver_date="2026-06-23",
        length_tier="long",
    )

    assert short_hit == "ep-short"
    assert long_hit == "ep-long"
    # 兩個請求查了兩次，呼叫紀錄都帶到正確的 tier。
    assert repo.find_calls == [("ai", "u1", "short"), ("ai", "u1", "long")]


# ── 3. generate_job 串接 ───────────────────────────────────────────


def _sample_script() -> ScriptJSON:
    """讀 scripts/loop_engineering.json 當合法 ScriptJSON 範本。"""
    root = Path(__file__).resolve().parents[2]
    return ScriptJSON.model_validate_json(
        (root / "scripts" / "loop_engineering.json").read_text(encoding="utf-8")
    )


def _sample_artifacts(tmp: Path) -> Any:
    from engine.media import EpisodeArtifacts

    mp3 = tmp / "episode.mp3"
    mp4 = tmp / "episode.mp4"
    mp3.write_bytes(b"FAKE_MP3")
    mp4.write_bytes(b"FAKE_MP4")
    cues = [Cue(index=1, speaker="Alex", text="hi", zh="嗨", start=0.0, end=1.0)]
    return EpisodeArtifacts(mp3_path=mp3, mp4_path=mp4, srt="1\n", vtt="WEBVTT\n", cues=cues)


class _GenRepoSpy:
    # Class-level 累積器：跨 instance 收集所有 upsert 呼叫，方便新測試斷言
    # 「同 big_topic/angle/tier 但不同 topic_type 會產生不同 key」。
    # 每個測試自己 reset，避免互相污染。
    calls: list[dict[str, Any]] = []

    def __init__(self) -> None:
        self.inserted: dict[str, Any] = {}
        self.updated: dict[str, Any] = {}
        self.deliveries: list[tuple[str, str, str]] = []

    async def upsert_episode(self, **kw: Any) -> tuple[str, bool]:
        self.inserted = kw
        _GenRepoSpy.calls.append(kw)
        # 第二元素＝already_rendered：新建恆為 False（需完整渲染）
        return "ep-new-id", False

    async def update_episode_keys(self, episode_id: str, **kw: Any) -> None:
        self.updated = {"episode_id": episode_id, **kw}

    async def insert_delivery(self, user_id: str, episode_id: str, deliver_date: str) -> bool:
        self.deliveries.append((user_id, episode_id, deliver_date))
        return True


def _patch_generate_job(
    monkeypatch: pytest.MonkeyPatch,
    *,
    script: ScriptJSON,
    repo_spy: _GenRepoSpy,
    write_raises: Exception | None = None,
) -> tuple[dict[str, Any], list[tuple[str, bytes, str]], dict[str, int]]:
    """把 LangGraph pod 的外部相依全 mock。

    回傳：
      mocks       — 直接傳給 run_generate_job 的 **kwargs（chat / chat_failover /
                    repo / r2 / renderer）
      uploads     — R2 put_object 呼叫記錄
      call_counts — 各 chat 的呼叫次數（驗證 failover 是否真的切到 chat_failover）
    """
    uploads: list[tuple[str, bytes, str]] = []

    def fake_put(key: str, data: bytes, content_type: str) -> None:
        uploads.append((key, data, content_type))

    class _FakeR2:
        put_object = staticmethod(fake_put)

    script_json = script.model_dump_json()
    # judge 預設給「過」的 verdict（threshold 0.6，五軸全給 0.8 過）
    passing_judge = json.dumps(
        {
            "hook_strength": 0.8,
            "informativeness": 0.8,
            "pacing": 0.8,
            "chemistry": 0.8,
            "groundedness": 0.8,
            "feedback": [],
        }
    )
    if write_raises is not None:
        # primary 撞限流，failover 補上合法 ScriptJSON
        chat = FakeChatModel(
            responses=[write_raises],
            judge_responses=[passing_judge],
        )
        chat_failover = FakeChatModel(
            responses=[script_json],
            judge_responses=[passing_judge],
        )
    else:
        chat = FakeChatModel(
            responses=[script_json],
            judge_responses=[passing_judge],
        )
        chat_failover = (
            FakeChatModel(
                responses=[script_json],
                judge_responses=[passing_judge],
            )
            if False
            else None
        )

    renderer = MockRenderer(make_mock_workdir())
    call_counts = {"primary": 0, "failover": 0}
    chat._call_count = 0  # type: ignore[attr-defined]
    if chat_failover is not None:
        chat_failover._call_count = 0  # type: ignore[attr-defined]

    mocks = {
        "chat": chat,
        "chat_failover": chat_failover,
        "repo": repo_spy,
        "r2": _FakeR2(),
        "renderer": renderer,
    }
    return mocks, uploads, call_counts


async def test_generate_job_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    script = _sample_script()
    repo_spy = _GenRepoSpy()
    mocks, uploads, _ = _patch_generate_job(monkeypatch, script=script, repo_spy=repo_spy)

    body = {
        "big_topic": "科技",
        "angle": "定義",
        "deliver_date": "2026-06-23",
        "user_ids": ["u1", "u2"],
    }
    episode_id = await generate_job.run_generate_job(body, **mocks)

    assert episode_id == "ep-new-id"
    # episode 分類：科技 → tech
    assert repo_spy.inserted["topic"] == "tech"
    assert repo_spy.inserted["big_topic"] == "科技"
    # R2 key 格式 episodes/{episode_id}/...
    keys = {u[0] for u in uploads}
    assert keys == {
        "episodes/ep-new-id/episode.mp3",
        "episodes/ep-new-id/episode.mp4",
        "episodes/ep-new-id/episode.srt",
    }
    # content-type 正確
    types = {u[0].rsplit(".", 1)[1]: u[2] for u in uploads}
    assert types == {"mp3": "audio/mpeg", "mp4": "video/mp4", "srt": "application/x-subrip"}
    # update_episode_keys 帶到 cues
    assert "cues" in repo_spy.updated
    # 兩位收件人都交付
    assert repo_spy.deliveries == [
        ("u1", "ep-new-id", "2026-06-23"),
        ("u2", "ep-new-id", "2026-06-23"),
    ]


async def test_generate_job_skips_render_when_already_rendered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """冪等：upsert 命中既有已渲染集 → 跳過渲染/上傳/回填，只補交付。"""
    script = _sample_script()
    repo_spy = _GenRepoSpy()
    mocks, uploads, _ = _patch_generate_job(monkeypatch, script=script, repo_spy=repo_spy)

    # 覆寫 upsert：回 already_rendered=True（模擬重投撞到已完成的集）
    async def upsert_existing(**kw: Any) -> tuple[str, bool]:
        repo_spy.inserted = kw
        return "ep-existing", True

    repo_spy.upsert_episode = upsert_existing  # type: ignore[method-assign]

    body = {
        "big_topic": "科技",
        "angle": "定義",
        "deliver_date": "2026-06-23",
        "user_ids": ["u1", "u2"],
    }
    episode_id = await generate_job.run_generate_job(body, **mocks)

    assert episode_id == "ep-existing"
    assert uploads == []  # 未重渲染、未重傳 R2（無孤兒物件）
    assert repo_spy.updated == {}  # 未重複回填
    # 交付仍照常（ON CONFLICT 冪等），兩位收件人都補到既有集
    assert repo_spy.deliveries == [
        ("u1", "ep-existing", "2026-06-23"),
        ("u2", "ep-existing", "2026-06-23"),
    ]


async def test_generate_job_passes_idempotency_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """冪等鍵尾段帶 length_tier + topic_type，避免同日同 big_topic 不同入口/長度互蓋。

    無 cluster_id 時冪等鍵形狀＝deliver_date:big_topic:angle:length_tier:topic_type。
    """
    script = _sample_script()
    repo_spy = _GenRepoSpy()
    mocks, _, _ = _patch_generate_job(monkeypatch, script=script, repo_spy=repo_spy)

    body = {
        "big_topic": "科技",
        "angle": "定義",
        "deliver_date": "2026-06-23",
        "user_ids": ["u1"],
    }
    await generate_job.run_generate_job(body, **mocks)
    # length_tier / topic_type 都未指定時分別預設 medium / evergreen。
    assert (
        repo_spy.inserted["idempotency_key"]
        == "2026-06-23:科技:定義:medium:evergreen"
    )


async def test_generate_job_idempotency_key_includes_topic_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """同 big_topic/angle/length_tier 但不同 topic_type 必須產生不同的冪等鍵。"""
    _GenRepoSpy.calls.clear()  # 隔離這個測試，別被共用 class-level 累積器污染
    script = _sample_script()
    repo_spy = _GenRepoSpy()
    mocks, _, _ = _patch_generate_job(monkeypatch, script=script, repo_spy=repo_spy)

    base = {
        "big_topic": "科技",
        "angle": "定義",
        "deliver_date": "2026-06-23",
        "user_ids": ["u1"],
        "length_tier": "medium",
    }
    news = await generate_job.run_generate_job({**base, "topic_type": "news"}, **mocks)
    topic = await generate_job.run_generate_job({**base, "topic_type": "topic"}, **mocks)

    assert news == "ep-new-id"
    assert topic == "ep-new-id"
    # 兩次呼叫都抓到 inserted（spy 會被覆蓋）；第二次呼叫是「預期冪等命中」但
    # 不在本測試重點——重點是 key 不同：後端 upsert 收到不同 key 不會視為重複。
    inserted_news, inserted_topic = _GenRepoSpy.calls[0], _GenRepoSpy.calls[1]
    assert inserted_news["idempotency_key"] != inserted_topic["idempotency_key"]
    assert inserted_news["idempotency_key"].endswith(":medium:news")
    assert inserted_topic["idempotency_key"].endswith(":medium:topic")


async def test_generate_job_degrade_gives_up(monkeypatch: pytest.MonkeyPatch) -> None:
    """failover_mode=degrade：限流直接放棄（raise RateLimitError），不落庫、不交付。"""
    script = _sample_script()
    repo_spy = _GenRepoSpy()
    mocks, _, _ = _patch_generate_job(
        monkeypatch, script=script, repo_spy=repo_spy, write_raises=RateLimitError("429")
    )
    settings = generate_job.get_settings().model_copy(
        update={"failover_mode": "degrade", "generation_engine": "minimax"}
    )

    body = {"big_topic": "ai", "deliver_date": "2026-06-23", "user_ids": ["u1"]}
    with pytest.raises(RateLimitError):
        await generate_job.run_generate_job(body, settings, **mocks)
    assert repo_spy.inserted == {}  # 沒落庫
    assert repo_spy.deliveries == []


async def test_generate_job_failover_switches_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    """failover_mode=failover：主引擎限流 → 切 api_key 重跑成功。"""
    script = _sample_script()
    repo_spy = _GenRepoSpy()
    mocks, _, _ = _patch_generate_job(
        monkeypatch, script=script, repo_spy=repo_spy, write_raises=RateLimitError("429")
    )
    settings = generate_job.get_settings().model_copy(
        update={"failover_mode": "failover", "generation_engine": "minimax"}
    )

    body = {"big_topic": "ai", "deliver_date": "2026-06-23", "user_ids": ["u1"]}
    episode_id = await generate_job.run_generate_job(body, settings, **mocks)
    assert episode_id == "ep-new-id"
    # primary 撞限流被切走，failover chat 應該被叫到
    assert mocks["chat_failover"] is not None
    assert mocks["chat_failover"]._call_count >= 1  # type: ignore[attr-defined]
    assert repo_spy.deliveries == [("u1", "ep-new-id", "2026-06-23")]


# ── 4. worker dispatch ─────────────────────────────────────────────


class FakeWorkerQueue:
    """模擬 pgmq：control/generate 各放預置訊息，記錄 delete/archive。"""

    def __init__(self) -> None:
        self.deleted: list[tuple[str, int]] = []
        self.archived: list[tuple[str, int]] = []
        self.control: list[Any] = []
        self.generate: list[Any] = []

    async def read(self, queue: str, vt: int) -> Any:
        bucket = self.control if queue == "control" else self.generate
        return bucket.pop(0) if bucket else None

    async def delete(self, queue: str, msg_id: int) -> bool:
        self.deleted.append((queue, msg_id))
        return True

    async def archive(self, queue: str, msg_id: int) -> bool:
        self.archived.append((queue, msg_id))
        return True


def _patch_worker(monkeypatch: pytest.MonkeyPatch, q: FakeWorkerQueue) -> None:
    from engine import worker

    monkeypatch.setattr(worker, "queue", q)
    monkeypatch.setattr(worker, "open_pool", _anoop)
    monkeypatch.setattr(worker, "close_pool", _anoop)


async def _anoop(*_: Any, **__: Any) -> None:
    return None


async def test_worker_process_success_deletes(monkeypatch: pytest.MonkeyPatch) -> None:
    from engine import worker

    q = FakeWorkerQueue()
    monkeypatch.setattr(worker, "queue", q)

    async def ok_handler(body: dict[str, Any]) -> None:
        return None

    msg = worker.Msg(msg_id=7, read_ct=1, body={"task": "x"})
    await worker._process("generate", msg, ok_handler, dead_letter_after=3)
    assert q.deleted == [("generate", 7)]
    assert q.archived == []


async def test_worker_process_transient_failure_no_delete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from engine import worker

    q = FakeWorkerQueue()
    monkeypatch.setattr(worker, "queue", q)

    async def boom(body: dict[str, Any]) -> None:
        raise RuntimeError("transient")

    msg = worker.Msg(msg_id=8, read_ct=1, body={})  # read_ct < 3
    await worker._process("generate", msg, boom, dead_letter_after=3)
    assert q.deleted == []  # 不刪 → vt 到期重投
    assert q.archived == []


async def test_worker_process_dead_letter_archives(monkeypatch: pytest.MonkeyPatch) -> None:
    from engine import worker

    q = FakeWorkerQueue()
    monkeypatch.setattr(worker, "queue", q)

    async def boom(body: dict[str, Any]) -> None:
        raise RuntimeError("poison")

    msg = worker.Msg(msg_id=9, read_ct=3, body={})  # read_ct >= 3
    await worker._process("generate", msg, boom, dead_letter_after=3)
    assert q.archived == [("generate", 9)]
    assert q.deleted == []


async def test_worker_generate_timeout_no_delete(monkeypatch: pytest.MonkeyPatch) -> None:
    """generate 超時 → TimeoutError 往外拋 → 不 delete（read_ct 低時留重投）。"""
    from engine import worker

    q = FakeWorkerQueue()
    monkeypatch.setattr(worker, "queue", q)

    async def slow(body: dict[str, Any]) -> None:
        await asyncio.sleep(0.05)

    async def handler(body: dict[str, Any]) -> None:
        await worker._handle_generate(body, timeout_sec=0)  # 立即超時

    monkeypatch.setattr(worker, "run_generate_job", slow)
    msg = worker.Msg(msg_id=10, read_ct=1, body={"big_topic": "ai"})
    await worker._process("generate", msg, handler, dead_letter_after=3)
    assert q.deleted == []
    assert q.archived == []


async def test_worker_loop_routes_control_then_generate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """主迴圈：control 優先處理；處理完一輪後 shutdown 退出。"""
    from engine import worker

    q = FakeWorkerQueue()
    _patch_worker(monkeypatch, q)

    handled: list[str] = []

    async def fake_control(body: dict[str, Any]) -> None:
        handled.append(f"control:{body.get('task')}")

    async def fake_generate(body: dict[str, Any]) -> None:
        handled.append(f"generate:{body.get('big_topic')}")

    monkeypatch.setattr(worker, "_handle_control", fake_control)
    monkeypatch.setattr(worker, "run_generate_job", fake_generate)

    q.control.append(worker.Msg(msg_id=1, read_ct=1, body={"task": "evergreen"}))
    q.generate.append(worker.Msg(msg_id=2, read_ct=1, body={"big_topic": "ai"}))

    shutdown = worker._Shutdown()

    # 自製 sleep：佇列空時觸發關閉，避免無限迴圈
    async def stop_sleep(_: float) -> None:
        shutdown.requested = True

    monkeypatch.setattr(worker.asyncio, "sleep", stop_sleep)

    await worker.run_worker(shutdown)

    assert handled == ["control:evergreen", "generate:ai"]
    assert ("control", 1) in q.deleted
    assert ("generate", 2) in q.deleted
