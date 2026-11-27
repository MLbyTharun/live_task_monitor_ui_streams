"""Persistent UI settings: active project and its camera list."""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

log = logging.getLogger("ltm.settings")


class SettingsStore:
    """Stores the project chosen in the dashboard and that project's cameras.

    Cameras belong to a project: switching projects swaps the active camera
    set, and switching back restores the previous one.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        self._data: dict[str, Any] = {"active_project": None, "projects": {}}
        self._load()

    # ------------------------------------------------------------- project
    @property
    def active_project(self) -> dict[str, str] | None:
        with self._lock:
            active = self._data.get("active_project")
            if not isinstance(active, dict) or not active.get("id"):
                return None
            return {"id": str(active["id"]), "name": str(active.get("name") or active["id"])}

    def set_active_project(self, project_id: str, name: str | None = None) -> None:
        project_id = project_id.strip()
        if not project_id:
            raise ValueError("project_id is required")
        with self._lock:
            project = self._project(project_id, create=True)
            if name:
                project["name"] = name.strip()
            self._data["active_project"] = {
                "id": project_id,
                "name": project.get("name") or project_id,
            }
            project["updated_at"] = datetime.now(UTC).isoformat()
            self._save_locked()

    # ------------------------------------------------------------- cameras
    def cameras(self, project_id: str) -> list[dict[str, Any]]:
        with self._lock:
            project = self._data.get("projects", {}).get(project_id)
            if not isinstance(project, dict):
                return []
            cameras = project.get("cameras")
            if not isinstance(cameras, list):
                return []
            return [dict(camera) for camera in cameras if isinstance(camera, dict)]

    def set_cameras(self, project_id: str, cameras: list[dict[str, Any]]) -> None:
        with self._lock:
            project = self._project(project_id, create=True)
            project["cameras"] = [dict(camera) for camera in cameras]
            project["cameras_seeded"] = True
            project["updated_at"] = datetime.now(UTC).isoformat()
            self._save_locked()

    def cameras_seeded(self, project_id: str) -> bool:
        """True once this project's camera list has been created from config."""
        with self._lock:
            project = self._data.get("projects", {}).get(project_id)
            return bool(isinstance(project, dict) and project.get("cameras_seeded"))

    def project_name(self, project_id: str) -> str | None:
        with self._lock:
            project = self._data.get("projects", {}).get(project_id)
            if isinstance(project, dict) and project.get("name"):
                return str(project["name"])
            return None

    # ------------------------------------------------------------- storage
    def _project(self, project_id: str, create: bool = False) -> dict[str, Any]:
        projects = self._data.setdefault("projects", {})
        project = projects.get(project_id)
        if not isinstance(project, dict):
            if not create:
                return {}
            project = {"name": project_id, "cameras": []}
            projects[project_id] = project
        project.setdefault("cameras", [])
        return project

    def _load(self) -> None:
        if not self.path.is_file():
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            log.warning("ignoring unreadable settings file %s: %s", self.path, exc)
            return
        if not isinstance(payload, dict):
            log.warning("ignoring settings file %s: expected an object", self.path)
            return
        if isinstance(payload.get("projects"), dict):
            self._data["projects"] = payload["projects"]
        if isinstance(payload.get("active_project"), dict):
            self._data["active_project"] = payload["active_project"]

    def _save_locked(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        payload = {
            "updated_at": datetime.now(UTC).isoformat(),
            "active_project": self._data.get("active_project"),
            "projects": self._data.get("projects", {}),
        }
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)
