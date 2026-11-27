"""Deterministic fake model for demos and tests (no GPU, no credentials)."""

from __future__ import annotations

import asyncio
from collections import defaultdict

from ..config import VisionConfig
from ..schemas import Snapshot, Task, Verdict
from .base import VisionModel


class MockVisionModel(VisionModel):
    """Completes each task after ``vision.mock_complete_after`` analyses."""

    name = "mock"

    def __init__(self, config: VisionConfig) -> None:
        self.config = config
        self.after = max(1, config.mock_complete_after)
        self._counts: dict[str, int] = defaultdict(int)

    async def analyze(
        self,
        task: Task,
        frames: list[Snapshot],
        system_prompt: str,
        user_prompt: str,
    ) -> Verdict:
        await asyncio.sleep(0.15)
        self._counts[task.id] += 1
        count = self._counts[task.id]
        completed = count >= self.after
        cameras = ", ".join(sorted({frame.stream_name for frame in frames})) or "none"

        return Verdict(
            task_completed=completed,
            confidence=0.92 if completed else 0.2,
            reasoning=(
                f"Mock analysis {count}/{self.after} for '{task.title}' using "
                f"{len(frames)} frame(s) from {cameras}."
            ),
            evidence=[
                f"Mock provider, analysis #{count}",
                f"{len(frames)} frame(s), cameras: {cameras}",
            ],
            model=self.name,
            latency_ms=150,
        )

    async def ping(self) -> str:
        return f"mock provider ready (completes after {self.after} analyses per task)"
