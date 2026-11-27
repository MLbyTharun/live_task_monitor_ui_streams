"""Task source interface and factory."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..config import Config
from ..schemas import Task


class TaskSourceError(RuntimeError):
    """Raised when the task list cannot be fetched or parsed."""


@runtime_checkable
class TaskSource(Protocol):
    """Anything that can return an ordered list of tasks."""

    name: str

    def fetch_tasks(self) -> list[Task]:
        """Return every task, already sorted by execution order."""
        ...


def build_task_source(config: Config) -> TaskSource:
    if config.tasks.source == "static":
        from .static import StaticTaskSource

        return StaticTaskSource(config.tasks)
    from .hyperreality import HyperRealityTaskSource

    return HyperRealityTaskSource(config.tasks.hyperreality)
