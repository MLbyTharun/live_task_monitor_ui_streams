"""Prompt templates loaded from disk and rendered per analysis."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

from .config import ConfigError
from .schemas import Snapshot, Task

_PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}")

_ALLOWED = {
    "task_title",
    "task_description",
    "task_instructions",
    "expected_time",
    "success_message",
    "stream_list",
    "timestamp",
}


class PromptLibrary:
    """Loads the system prompt and the per-task verification prompt."""

    def __init__(self, system_file: Path, verify_file: Path) -> None:
        self.system_file = Path(system_file)
        self.verify_file = Path(verify_file)
        self._system = self._read(self.system_file)
        self._verify = self._read(self.verify_file)
        unknown = set(_PLACEHOLDER_RE.findall(self._verify)) - _ALLOWED
        if unknown:
            raise ConfigError(
                f"unknown placeholders in {self.verify_file}: {', '.join(sorted(unknown))}. "
                f"Allowed: {', '.join(sorted(_ALLOWED))}"
            )

    @staticmethod
    def _read(path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise ConfigError(f"cannot read prompt file {path}: {exc}") from exc

    @property
    def system_prompt(self) -> str:
        return self._system

    def build(self, task: Task, frames: list[Snapshot]) -> tuple[str, str]:
        """Return ``(system_prompt, user_prompt)`` for one analysis."""
        names: list[str] = []
        for frame in frames:
            label = f"- {frame.stream_name} ({frame.stream_id})"
            if label not in names:
                names.append(label)
        if not names:
            names.append("- (no cameras)")

        captured = max((frame.captured_at for frame in frames), default=datetime.now(UTC))
        values = {
            "task_title": task.title,
            "task_description": task.description or "not specified",
            "task_instructions": task.instructions or "not specified",
            "expected_time": (
                f"{task.expected_time_sec:g} seconds"
                if task.expected_time_sec is not None
                else "not specified"
            ),
            "success_message": task.success_message or "not specified",
            "stream_list": "\n".join(names),
            "timestamp": captured.isoformat(timespec="seconds"),
        }

        def replace(match: re.Match[str]) -> str:
            return str(values.get(match.group(1), ""))

        return self._system, _PLACEHOLDER_RE.sub(replace, self._verify)
