"""Task source backed by the HyperReality authoring API."""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from ..config import HyperRealityConfig
from ..schemas import Task
from .base import TaskSourceError

log = logging.getLogger("ltm.tasks.hyperreality")

_TASKS_PATH = "/api/authoringv2/tasks"
_RETRY_STATUS = {429, 500, 502, 503, 504}


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


class HyperRealityTaskSource:
    """Fetches ordered tasks from ``GET /api/authoringv2/tasks``.

    Results are scoped by project and action block, ``wip`` tasks are skipped,
    and the order is normalised client-side so the local task queue is stable
    even if the server ignores a query parameter.
    """

    name = "hyperreality"

    def __init__(
        self,
        config: HyperRealityConfig,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.config = config
        self._transport = transport

    # ------------------------------------------------------------------ API
    def fetch_tasks(self) -> list[Task]:
        cfg = self.config
        if not cfg.base_url.strip():
            raise TaskSourceError("HR_BASE_URL is empty")
        if not cfg.token.strip():
            raise TaskSourceError(
                "HR_TOKEN is empty — paste a valid authoring API token into .env"
            )

        url = cfg.base_url.rstrip("/") + _TASKS_PATH
        params: dict[str, Any] = {"page": 1, "limit": cfg.page_size}
        if cfg.project_id:
            params["projectId"] = cfg.project_id
        if cfg.action_block_id:
            params["actionBlockId"] = cfg.action_block_id

        tasks: list[Task] = []
        with httpx.Client(
            timeout=cfg.timeout_seconds,
            headers=self._headers(),
            transport=self._transport,
            follow_redirects=True,
        ) as client:
            page = 1
            while True:
                params["page"] = page
                payload = self._get_page(client, url, params)
                rows = payload.get("data")
                if not isinstance(rows, list):
                    raise TaskSourceError(
                        f"unexpected response from {url}: missing 'data' list"
                    )
                tasks.extend(self._map(row) for row in rows if isinstance(row, dict))

                meta = payload.get("meta") or {}
                total_pages = meta.get("totalPages") or meta.get("pages") or 1
                try:
                    total_pages = int(total_pages)
                except (TypeError, ValueError):
                    total_pages = 1
                if page >= total_pages:
                    break
                page += 1

        if not cfg.include_wip:
            tasks = [task for task in tasks if not task.wip]
        if cfg.project_id:
            tasks = [
                task
                for task in tasks
                if task.project_id in (None, "", cfg.project_id)
            ]
        if cfg.action_block_id:
            tasks = [
                task
                for task in tasks
                if task.action_block_id in (None, "", cfg.action_block_id)
            ]

        tasks.sort(key=lambda task: (task.order, task.title.lower()))
        log.info("fetched %d task(s) from HyperReality", len(tasks))
        return tasks

    # -------------------------------------------------------------- helpers
    def list_projects(self) -> list[dict[str, str]]:
        """Return projects visible to the configured organisation."""
        cfg = self.config
        if not cfg.base_url.strip():
            raise TaskSourceError("HR_BASE_URL is empty")
        if not cfg.token.strip():
            raise TaskSourceError(
                "HR_TOKEN is empty — paste a valid authoring API token into .env"
            )

        url = cfg.base_url.rstrip("/") + "/api/authoringv2/projects"
        params: dict[str, Any] = {"page": 1, "limit": 200}
        if cfg.organization_id:
            params["organization"] = cfg.organization_id

        with httpx.Client(
            timeout=cfg.timeout_seconds,
            headers=self._headers(),
            transport=self._transport,
            follow_redirects=True,
        ) as client:
            payload = self._get_page(client, url, params)

        rows = payload.get("data")
        if not isinstance(rows, list):
            raise TaskSourceError(
                f"unexpected response from {url}: missing 'data' list"
            )

        projects: list[dict[str, str]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            project_id = row.get("_id") or row.get("id")
            if not project_id:
                continue
            projects.append(
                {
                    "id": str(project_id),
                    "name": str(row.get("name") or project_id),
                }
            )
        projects.sort(key=lambda item: item["name"].lower())
        return projects

    def _headers(self) -> dict[str, str]:
        cfg = self.config
        headers = {
            "Authorization": f"Bearer {cfg.token}",
            "Accept": "application/json",
            "Referer": cfg.base_url.rstrip("/") + "/",
        }
        if cfg.organization_id:
            headers["x-organization-id"] = cfg.organization_id
        return headers

    def _get_page(
        self, client: httpx.Client, url: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        delay = 1.0
        last_error: str = ""
        for attempt in range(3):
            try:
                response = client.get(url, params=params)
            except httpx.HTTPError as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                if attempt < 2:
                    time.sleep(delay)
                    delay *= 2
                    continue
                raise TaskSourceError(f"cannot reach {url}: {last_error}") from exc

            if response.status_code in _RETRY_STATUS and attempt < 2:
                retry_after = response.headers.get("Retry-After", "")
                time.sleep(float(retry_after) if retry_after.isdigit() else delay)
                delay *= 2
                continue
            if response.status_code == 401:
                raise TaskSourceError(
                    "HyperReality rejected the token (HTTP 401). "
                    "HR_TOKEN is missing, expired, or from another environment."
                )
            if response.status_code >= 400:
                snippet = response.text[:300].replace("\n", " ")
                raise TaskSourceError(
                    f"GET {url} -> HTTP {response.status_code}: {snippet}"
                )
            try:
                payload = response.json()
            except ValueError as exc:
                raise TaskSourceError(
                    f"GET {url} returned non-JSON content"
                ) from exc
            if not isinstance(payload, dict):
                raise TaskSourceError(f"GET {url} returned an unexpected payload")
            return payload

        raise TaskSourceError(f"GET {url} failed: {last_error or 'unknown error'}")

    def _map(self, row: dict[str, Any]) -> Task:
        title = _text(row.get("title")) or _text(row.get("name")) or "Untitled task"
        description = _text(row.get("description")) or _text(row.get("text"))
        instructions = "\n".join(
            part
            for part in (
                _text(row.get("textInstructions")),
                _text(row.get("promptInstructions")),
                _text(row.get("videoInstructions")),
                _text(row.get("imageInstructions")),
            )
            if part
        )

        try:
            order = float(row.get("order") or 0)
        except (TypeError, ValueError):
            order = 0.0
        try:
            expected = row.get("expectedTimeSec")
            expected = float(expected) if expected is not None else None
        except (TypeError, ValueError):
            expected = None

        return Task(
            id=str(row.get("_id") or row.get("id") or ""),
            title=title,
            description=description,
            instructions=instructions,
            order=order,
            action_type=row.get("actionType"),
            expected_time_sec=expected,
            success_message=_text(row.get("successMessage")) or None,
            wip=bool(row.get("wip", False)),
            project_id=row.get("projectId"),
            action_block_id=row.get("actionBlockId"),
            raw=row,
        )
