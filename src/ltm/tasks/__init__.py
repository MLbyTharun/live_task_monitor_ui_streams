"""Task source factory helpers."""

from __future__ import annotations

from ..config import Config
from ..schemas import Task
from .base import TaskSource, TaskSourceError, build_task_source
from .hyperreality import HyperRealityTaskSource


def build_project_source(config: Config, project_id: str) -> TaskSource:
    """HyperReality source scoped to one project (no action-block filter)."""
    settings = config.tasks.hyperreality.model_copy(
        update={"project_id": project_id, "action_block_id": ""}
    )
    return HyperRealityTaskSource(settings)


__all__ = [
    "HyperRealityTaskSource",
    "Task",
    "TaskSource",
    "TaskSourceError",
    "build_project_source",
    "build_task_source",
]
