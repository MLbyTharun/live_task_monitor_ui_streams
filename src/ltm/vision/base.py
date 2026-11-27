"""Vision model interface."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..schemas import Snapshot, Task, Verdict


class VisionError(RuntimeError):
    """Raised when the model cannot be reached or returns an unusable answer."""


class VisionModel(ABC):
    """A multimodal model that judges one task from a set of frames."""

    name: str = "vision"

    @abstractmethod
    async def analyze(
        self,
        task: Task,
        frames: list[Snapshot],
        system_prompt: str,
        user_prompt: str,
    ) -> Verdict:
        """Return a verdict for ``task`` given the latest frames."""

    async def close(self) -> None:
        """Release network resources."""
        return None

    async def ping(self) -> str:
        """Check connectivity; return a short human-readable status."""
        return "ready"
