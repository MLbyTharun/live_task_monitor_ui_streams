from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from ltm.config import Config
from ltm.engine import TaskEngine
from ltm.events import EventBus
from ltm.schemas import Snapshot, Task, Verdict
from ltm.state import StateStore


class FakeSource:
    name = "fake"

    def __init__(self, tasks):
        self.tasks = tasks

    def fetch_tasks(self):
        return list(self.tasks)


class SequenceVision:
    name = "fake-vision"

    def __init__(self, verdicts):
        self.verdicts = list(verdicts)

    async def analyze(self, task, frames, system_prompt, user_prompt):
        return self.verdicts.pop(0)

    async def close(self):
        pass


class FakePrompts:
    def build(self, task, frames):
        return "system", f"task: {task.title}"


class FakeStreams:
    def __init__(self):
        self.frame = Snapshot(
            stream_id="cam1",
            stream_name="Camera 1",
            captured_at=datetime.now(UTC),
            seq=1,
            jpeg=b"jpeg-bytes",
            width=640,
            height=360,
        )

    def snapshot(self, **kwargs):
        return [self.frame]

    def statuses(self):
        return []

    def workers(self):
        return []


def build_engine(tmp_path, verdicts):
    config = Config.model_validate(
        {
            "app": {"data_dir": str(tmp_path / "data")},
            "vision": {"provider": "mock"},
            "analysis": {
                "confirmation_votes": 2,
                "min_confidence": 0.6,
                "save_evidence": False,
                "auto_start": False,
            },
            "streams": [],
        }
    )
    config.root = tmp_path
    tasks = [
        Task(id="t1", title="First task", order=1),
        Task(id="t2", title="Second task", order=2),
        Task(id="t3", title="Third task", order=3),
    ]
    state = StateStore(tmp_path / "data" / "state.json")
    engine = TaskEngine(
        config=config,
        task_source=FakeSource(tasks),
        vision=SequenceVision(verdicts),
        streams=FakeStreams(),
        prompts=FakePrompts(),
        bus=EventBus(),
        state=state,
    )
    return engine, state


def test_two_consecutive_votes_advance_and_persist(tmp_path):
    verdicts = [
        Verdict(task_completed=True, confidence=0.9, reasoning="seen"),
        Verdict(task_completed=True, confidence=0.9, reasoning="seen again"),
    ]
    engine, state = build_engine(tmp_path, verdicts)

    async def scenario():
        await engine.refresh_tasks()
        assert engine.current_task.id == "t1"
        assert engine.next_task.id == "t2"

        first = await engine.analyze_now()
        assert first is not None and first.confirmed is False
        assert engine.current_task.id == "t1"

        second = await engine.analyze_now()
        assert second is not None and second.confirmed is True
        assert engine.current_task.id == "t2"
        assert engine.next_task.id == "t3"
        assert state.completed_ids == {"t1"}

        snapshot = engine.state()
        assert snapshot["progress"] == {"completed": 1, "total": 3, "remaining": 2}
        assert snapshot["current_task"]["id"] == "t2"
        assert snapshot["next_task"]["id"] == "t3"

    asyncio.run(scenario())

    reloaded = StateStore(tmp_path / "data" / "state.json")
    assert reloaded.completed_ids == {"t1"}


def test_manual_completion_and_reset(tmp_path):
    engine, _ = build_engine(tmp_path, [])

    async def scenario():
        await engine.refresh_tasks()
        await engine.mark_completed("t1", reason="test")
        assert engine.current_task.id == "t2"

        await engine.mark_completed("t2", reason="test")
        assert engine.current_task.id == "t3"

        await engine.reset_progress()
        assert engine.current_task.id == "t1"
        assert engine.progress() == {"completed": 0, "total": 3, "remaining": 3}

    asyncio.run(scenario())


def test_no_frames_records_an_error(tmp_path):
    engine, _ = build_engine(
        tmp_path, [Verdict(task_completed=True, confidence=0.9)]
    )
    engine._streams.snapshot = lambda **kwargs: []

    async def scenario():
        await engine.refresh_tasks()
        record = await engine.analyze_now()
        assert record is not None
        assert record.error
        assert record.verdict is None
        assert engine.current_task.id == "t1"

    asyncio.run(scenario())
