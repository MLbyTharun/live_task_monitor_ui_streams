"""Task source backed by a local JSON file — useful for demos and tests."""

from __future__ import annotations

import json
from typing import Any

from ..config import TasksConfig
from ..schemas import Task
from .base import TaskSourceError


class StaticTaskSource:
    name = "static"

    def __init__(self, config: TasksConfig) -> None:
        self.path = config.static_file

    def fetch_tasks(self) -> list[Task]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise TaskSourceError(f"cannot read static task file {self.path}: {exc}") from exc
        except ValueError as exc:
            raise TaskSourceError(f"invalid JSON in {self.path}: {exc}") from exc

        rows: Any = payload.get("tasks") if isinstance(payload, dict) else payload
        if not isinstance(rows, list):
            raise TaskSourceError(f"{self.path} must contain a list of tasks")

        tasks = [
            self._map(row, index)
            for index, row in enumerate(rows)
            if isinstance(row, dict) and not row.get("wip")
        ]
        tasks.sort(key=lambda task: (task.order, task.title.lower()))
        return tasks

    def _map(self, row: dict[str, Any], index: int) -> Task:
        def number(key: str, default: float = 0.0) -> float:
            try:
                return float(row.get(key, default) or default)
            except (TypeError, ValueError):
                return default

        return Task(
            id=str(row.get("id") or row.get("_id") or f"task-{index + 1}"),
            title=str(row.get("title") or f"Task {index + 1}"),
            description=str(row.get("description") or row.get("text") or ""),
            instructions=str(row.get("instructions") or row.get("textInstructions") or ""),
            order=number("order", float(index + 1)),
            action_type=row.get("action_type") or row.get("actionType"),
            expected_time_sec=(
                number("expected_time_sec") if row.get("expected_time_sec") is not None else None
            ),
            success_message=row.get("success_message") or row.get("successMessage"),
            wip=bool(row.get("wip", False)),
            raw=row,
        )
