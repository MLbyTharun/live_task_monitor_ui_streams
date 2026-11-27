"""Small JSON state store: which tasks have already been completed.

Task ids are namespaced per project so switching projects keeps independent
progress. The file is backwards compatible with the original flat layout.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import UTC, datetime
from pathlib import Path

log = logging.getLogger("ltm.state")

_SEPARATOR = "::"


class StateStore:
    """Persists completed task ids so a restart does not redo finished work."""

    def __init__(self, path: Path, namespace: str | None = None) -> None:
        self.path = Path(path)
        self.namespace = namespace
        self._lock = threading.Lock()
        self._completed: dict[str, str] = {}
        self._load()

    def set_namespace(self, namespace: str | None) -> None:
        with self._lock:
            self.namespace = namespace.strip() if namespace else None

    @property
    def completed_ids(self) -> set[str]:
        with self._lock:
            return {self._unkey(key) for key in self._completed if self._owns(key)}

    def is_completed(self, task_id: str) -> bool:
        with self._lock:
            return self._key(task_id) in self._completed

    def mark_completed(self, task_id: str, at: datetime | None = None) -> None:
        timestamp = (at or datetime.now(UTC)).isoformat()
        with self._lock:
            self._completed[self._key(task_id)] = timestamp
            self._save_locked()

    def clear(self) -> None:
        """Clear completion progress for the current namespace only."""
        with self._lock:
            self._completed = {
                key: value for key, value in self._completed.items() if not self._owns(key)
            }
            self._save_locked()

    def clear_all(self) -> None:
        with self._lock:
            self._completed.clear()
            self._save_locked()

    def as_dict(self) -> dict[str, str]:
        with self._lock:
            return {
                self._unkey(key): value
                for key, value in self._completed.items()
                if self._owns(key)
            }

    # ------------------------------------------------------------------
    def _key(self, task_id: str) -> str:
        return f"{self.namespace}{_SEPARATOR}{task_id}" if self.namespace else task_id

    def _owns(self, key: str) -> bool:
        if not self.namespace:
            return _SEPARATOR not in key
        return key.startswith(f"{self.namespace}{_SEPARATOR}")

    def _unkey(self, key: str) -> str:
        if not self.namespace:
            return key
        return key.split(_SEPARATOR, 1)[1]

    def _load(self) -> None:
        if not self.path.is_file():
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            completed = payload.get("completed", payload)
            if not isinstance(completed, dict):
                raise TypeError("expected an object of task_id -> timestamp")
            self._completed = {str(key): str(value) for key, value in completed.items()}
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            log.warning("ignoring unreadable state file %s: %s", self.path, exc)

    def _save_locked(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        payload = {
            "updated_at": datetime.now(UTC).isoformat(),
            "completed": self._completed,
        }
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)
