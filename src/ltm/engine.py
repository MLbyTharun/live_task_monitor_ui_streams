"""The analysis engine: task queue, sampling, verification and completion."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections import deque
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import Config, StreamConfig
from .events import EventBus, EventLog
from .prompts import PromptLibrary
from .schemas import AnalysisRecord, Snapshot, Task, Verdict
from .settings import SettingsStore
from .state import StateStore
from .streams.manager import StreamManager
from .tasks.base import TaskSource, TaskSourceError
from .tracker import CompletionTracker
from .vision.base import VisionError, VisionModel

log = logging.getLogger("ltm.engine")


class TaskEngine:
    """Runs one active task at a time and advances when the model agrees."""

    def __init__(
        self,
        config: Config,
        task_source: TaskSource,
        vision: VisionModel,
        streams: StreamManager,
        prompts: PromptLibrary,
        bus: EventBus,
        state: StateStore,
        event_log: EventLog | None = None,
        settings: SettingsStore | None = None,
    ) -> None:
        self._config = config
        self._base_task_source = task_source
        self._tasks_source = task_source
        self._vision = vision
        self._streams = streams
        self._prompts = prompts
        self._bus = bus
        self._state = state
        self._event_log = event_log
        self._settings = settings

        self._project: dict[str, str] | None = None
        if settings is not None:
            self._project = settings.active_project
        if self._project is not None:
            self._state.set_namespace(self._project["id"])
            self._tasks_source = self._source_for_project(self._project["id"])

        self._tasks: list[Task] = []
        self._current: Task | None = None
        self._tracker = CompletionTracker(
            votes_required=config.analysis.confirmation_votes,
            min_confidence=config.analysis.min_confidence,
        )
        self._history: deque[AnalysisRecord] = deque(
            maxlen=config.analysis.history_size
        )
        self._last_record: AnalysisRecord | None = None
        self._seq = 0

        self._running = False
        self._started_at: datetime | None = None
        self._loop_task: asyncio.Task[None] | None = None
        self._analysis_lock = asyncio.Lock()
        self._refresh_lock = asyncio.Lock()
        self._last_refresh = 0.0
        self._last_completion = 0.0
        self._all_done_announced = False

    # ---------------------------------------------------------------- life
    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._started_at = datetime.now(UTC)
        self._prepare_project()
        if self._project is not None:
            self._streams.apply(
                self._project_cameras(self._project["id"]), start=False
            )
        self._streams.start_all()
        await self.refresh_tasks(raise_on_error=False)

        self._emit(
            "engine_started",
            {
                "model": self._vision.name,
                "task_source": self._tasks_source.name,
                "project": self._project,
                "streams": [stream.id for stream in self._streams.workers()],
            },
        )
        log.info(
            "engine started | model=%s | tasks=%s | project=%s | streams=%s",
            self._vision.name,
            self._tasks_source.name,
            self._project["name"] if self._project else "none",
            ", ".join(stream.id for stream in self._streams.workers()) or "none",
        )

        if self._config.analysis.auto_start:
            self._loop_task = asyncio.create_task(
                self._run_loop(), name="ltm-analysis-loop"
            )

    async def stop(self) -> None:
        self._running = False
        if self._loop_task is not None:
            self._loop_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._loop_task
            self._loop_task = None
        self._streams.stop_all()
        await self._vision.close()
        self._emit("engine_stopped", {})
        log.info("engine stopped")

    # ----------------------------------------------------- project/cameras
    @property
    def project(self) -> dict[str, str] | None:
        return self._project

    def _source_for_project(self, project_id: str | None) -> TaskSource:
        if self._config.tasks.source == "hyperreality" and project_id:
            from .tasks import build_project_source

            return build_project_source(self._config, project_id)
        return self._base_task_source

    def _default_project(self) -> dict[str, str] | None:
        if self._config.tasks.source == "hyperreality":
            project_id = self._config.tasks.hyperreality.project_id.strip()
            return {"id": project_id, "name": project_id} if project_id else None
        return {"id": "local", "name": "Local task list"}

    def _prepare_project(self) -> None:
        """Pick the persisted project (or a config default) before starting."""
        if self._project is None:
            default = self._default_project()
            if default is None:
                return
            if self._settings is not None:
                self._settings.set_active_project(default["id"], default["name"])
                self._project = self._settings.active_project or default
            else:
                self._project = default

        self._state.set_namespace(self._project["id"])
        if self._config.tasks.source == "hyperreality":
            self._tasks_source = self._source_for_project(self._project["id"])

    def _camera_models(self, rows: list[dict[str, Any]]) -> list[StreamConfig]:
        cameras: list[StreamConfig] = []
        for row in rows:
            try:
                cameras.append(StreamConfig.model_validate(row))
            except Exception as exc:
                log.warning("ignoring invalid saved camera %s: %s", row.get("id"), exc)
        return cameras

    def _project_cameras(self, project_id: str) -> list[StreamConfig]:
        """Stored cameras for a project, seeded from config on first use."""
        if self._settings is None:
            return list(self._config.streams)
        if not self._settings.cameras_seeded(project_id):
            seed = [camera.model_dump(mode="json") for camera in self._config.streams]
            self._settings.set_cameras(project_id, seed)
            return list(self._config.streams)
        return self._camera_models(self._settings.cameras(project_id))

    def _persist_cameras(self, project_id: str, cameras: list[StreamConfig]) -> None:
        if self._settings is not None:
            self._settings.set_cameras(
                project_id, [camera.model_dump(mode="json") for camera in cameras]
            )

    def _resolve_camera(self, camera: StreamConfig) -> StreamConfig:
        url = camera.url.strip()
        if url and "://" not in url and not url.isdigit():
            path = Path(url).expanduser()
            if not path.is_absolute():
                path = (self._config.root / path).resolve()
            camera = camera.model_copy(update={"url": str(path)})
        return camera

    def _require_project_id(self) -> str:
        if self._project is None:
            raise ValueError("select a project before managing cameras")
        return self._project["id"]

    def camera_configs(self) -> list[dict[str, Any]]:
        if self._project is None:
            return []
        return [
            camera.model_dump(mode="json")
            for camera in self._project_cameras(self._project["id"])
        ]

    async def set_project(self, project_id: str, name: str | None = None) -> None:
        """Switch the active project: tasks, progress and cameras all follow."""
        project_id = project_id.strip()
        if not project_id:
            raise ValueError("project id is required")

        async with self._analysis_lock:
            self._streams.stop_all()
            if self._settings is not None:
                self._settings.set_active_project(project_id, name)
                self._project = self._settings.active_project or {
                    "id": project_id,
                    "name": name or project_id,
                }
            else:
                self._project = {"id": project_id, "name": name or project_id}

            self._state.set_namespace(project_id)
            self._tasks_source = self._source_for_project(project_id)

            cameras = self._project_cameras(project_id)
            self._streams.apply(cameras, start=False)
            self._streams.start_all()

            self._tasks = []
            self._current = None
            self._tracker.reset()
            self._history.clear()
            self._last_record = None
            self._all_done_announced = False
            self._last_refresh = 0.0
            self._last_completion = 0.0

            self._emit(
                "project_changed",
                {
                    "project": self._project,
                    "cameras": len(cameras),
                    "progress": self.progress(),
                },
            )
            log.info(
                "project changed to %s (%s) with %d camera(s)",
                self._project["name"],
                project_id,
                len(cameras),
            )

        await self.refresh_tasks(raise_on_error=False)

    async def upsert_camera(self, camera: StreamConfig) -> StreamConfig:
        """Add or replace one camera for the active project."""
        camera = self._resolve_camera(camera)
        async with self._analysis_lock:
            project_id = self._require_project_id()
            cameras = self._project_cameras(project_id)
            for index, existing in enumerate(cameras):
                if existing.id == camera.id:
                    cameras[index] = camera
                    break
            else:
                cameras.append(camera)

            self._persist_cameras(project_id, cameras)
            self._streams.apply(cameras)
            self._emit(
                "cameras_changed",
                {
                    "action": "saved",
                    "project": self._project,
                    "camera": camera.model_dump(mode="json", exclude={"url"}),
                },
            )
        return camera

    async def delete_camera(self, camera_id: str) -> None:
        async with self._analysis_lock:
            project_id = self._require_project_id()
            cameras = self._project_cameras(project_id)
            remaining = [camera for camera in cameras if camera.id != camera_id]
            if len(remaining) == len(cameras):
                raise KeyError(f"unknown camera '{camera_id}'")

            self._persist_cameras(project_id, remaining)
            self._streams.apply(remaining)
            self._emit(
                "cameras_changed",
                {
                    "action": "deleted",
                    "project": self._project,
                    "camera": {"id": camera_id},
                },
            )

    async def test_camera(self, camera: StreamConfig) -> dict[str, Any]:
        """Probe a camera without saving it."""
        camera = self._resolve_camera(camera)
        if camera.type == "push":
            worker = self._streams.get(camera.id)
            if worker is None:
                return {
                    "ok": False,
                    "type": "push",
                    "error": "not active yet — save it, then open the dashboard camera",
                }
            status = worker.status()
            return {
                "ok": status.connected,
                "type": "push",
                "connected": status.connected,
                "frames": status.frames,
                "last_frame_at": (
                    status.last_frame_at.isoformat() if status.last_frame_at else None
                ),
                "error": status.error,
            }

        if not camera.url.strip():
            return {"ok": False, "type": "video", "error": "url is empty"}
        return await asyncio.to_thread(self._probe_video, camera)

    def _probe_video(self, camera: StreamConfig) -> dict[str, Any]:
        import cv2

        from .streams.video import safe_url

        url = camera.url.strip()
        source: str | int = int(url) if url.isdigit() else url
        params = [
            cv2.CAP_PROP_OPEN_TIMEOUT_MSEC,
            8000,
            cv2.CAP_PROP_READ_TIMEOUT_MSEC,
            8000,
        ]
        try:
            try:
                cap = cv2.VideoCapture(source, cv2.CAP_FFMPEG, params)
            except TypeError:  # older OpenCV without the params overload
                cap = cv2.VideoCapture(source)
            try:
                ok, frame = cap.read() if cap.isOpened() else (False, None)
            finally:
                cap.release()
        except Exception as exc:
            return {
                "ok": False,
                "type": "video",
                "error": f"{type(exc).__name__}: {exc}",
            }

        if not ok or frame is None:
            return {
                "ok": False,
                "type": "video",
                "error": f"no frame from {safe_url(camera.url)}",
            }
        height, width = frame.shape[:2]
        return {"ok": True, "type": "video", "width": width, "height": height}

    # --------------------------------------------------------------- tasks
    async def refresh_tasks(self, raise_on_error: bool = True) -> None:
        async with self._refresh_lock:
            try:
                tasks = await asyncio.to_thread(self._tasks_source.fetch_tasks)
            except TaskSourceError as exc:
                log.error("task refresh failed: %s", exc)
                self._emit("task_source_error", {"error": str(exc)})
                # Throttle retries to refresh_seconds even while failing.
                self._last_refresh = time.monotonic()
                if raise_on_error:
                    raise
                return

            self._tasks = tasks
            self._last_refresh = time.monotonic()
            self._emit(
                "tasks_refreshed",
                {"count": len(tasks), "source": self._tasks_source.name},
            )
            self._sync_current()

    def _pending_tasks(self) -> list[Task]:
        completed = self._state.completed_ids
        return [task for task in self._tasks if task.id not in completed]

    def _sync_current(self, force: bool = False) -> None:
        pending = self._pending_tasks()
        pending_ids = {task.id for task in pending}

        if (
            not force
            and self._current is not None
            and self._current.id in pending_ids
        ):
            return

        previous = self._current
        self._current = pending[0] if pending else None
        self._tracker.reset()

        if self._current is not None:
            self._all_done_announced = False
            if previous is None or previous.id != self._current.id:
                log.info('active task: "%s"', self._current.title)
                self._emit(
                    "task_activated",
                    {
                        "task": self._current.model_dump(mode="json"),
                        "progress": self.progress(),
                    },
                )
        elif self._tasks:
            self._announce_all_done()

    def _announce_all_done(self) -> None:
        if self._all_done_announced:
            return
        self._all_done_announced = True
        self._tracker.reset()
        log.info("🎉 all tasks completed")
        self._emit("all_tasks_completed", {"total": len(self._tasks)})

    async def mark_completed(
        self, task_id: str | None = None, reason: str = "manual"
    ) -> None:
        """Manually complete a task (dashboard button / operator override)."""
        async with self._analysis_lock:
            task = None
            if task_id:
                task = next((item for item in self._tasks if item.id == task_id), None)
            else:
                task = self._current
            if task is None:
                raise ValueError("no matching task to complete")
            if self._state.is_completed(task.id):
                return
            await self._complete_task(task, None, [], reason)

    async def reset_progress(self) -> None:
        async with self._analysis_lock:
            self._state.clear()
            self._tracker.reset()
            self._all_done_announced = False
            self._sync_current(force=True)
            self._emit("progress_reset", {"progress": self.progress()})
            log.info("progress reset")

    # ------------------------------------------------------------- analysis
    async def analyze_now(self) -> AnalysisRecord | None:
        async with self._analysis_lock:
            return await self._analyze_locked()

    async def _analyze_locked(self) -> AnalysisRecord | None:
        task = self._current
        if task is None:
            self._emit("analysis_skipped", {"reason": "no active task"})
            return None

        analysis_cfg = self._config.analysis
        try:
            frames = self._streams.snapshot(
                per_stream=analysis_cfg.frames_per_stream,
                max_side=self._config.vision.max_image_side,
                quality=self._config.vision.jpeg_quality,
                max_total=analysis_cfg.max_frames_per_request,
            )
        except Exception as exc:  # pragma: no cover - defensive
            log.exception("frame sampling failed")
            frames = []
            sampling_error = f"{type(exc).__name__}: {exc}"
        else:
            sampling_error = None

        if not frames:
            error = sampling_error or (
                "no frames available — check that streams are connected "
                "(dashboard shows per-stream status)"
            )
            record = self._record(task, None, 0, False, [], error=error)
            self._remember(record)
            self._emit(
                "analysis_error", {"task_id": task.id, "error": error}
            )
            log.warning("analysis skipped: %s", error)
            return record

        system_prompt, user_prompt = self._prompts.build(task, frames)
        started = time.perf_counter()
        try:
            verdict = await self._vision.analyze(
                task, frames, system_prompt, user_prompt
            )
        except VisionError as exc:
            error = str(exc)
            record = self._record(
                task, None, 0, False, [f.stream_id for f in frames], error=error
            )
            self._remember(record)
            self._emit("analysis_error", {"task_id": task.id, "error": error})
            log.warning("vision error: %s", error)
            return record
        except Exception as exc:  # pragma: no cover - defensive
            log.exception("unexpected vision failure")
            error = f"{type(exc).__name__}: {exc}"
            record = self._record(
                task, None, 0, False, [f.stream_id for f in frames], error=error
            )
            self._remember(record)
            self._emit("analysis_error", {"task_id": task.id, "error": error})
            return record

        if verdict.latency_ms is None:
            verdict.latency_ms = int((time.perf_counter() - started) * 1000)
        if verdict.model is None:
            verdict.model = self._vision.name

        confirmed = self._tracker.add(verdict)
        record = self._record(
            task,
            verdict,
            self._tracker.votes,
            confirmed,
            [frame.stream_id for frame in frames],
        )
        self._remember(record)
        self._emit("analysis", record.model_dump(mode="json"))

        log.info(
            'analysis | task="%s" | completed=%s confidence=%.2f votes=%d/%d | %s',
            task.title,
            verdict.task_completed,
            verdict.confidence,
            self._tracker.votes,
            self._tracker.votes_required,
            verdict.reasoning[:160],
        )

        if confirmed:
            await self._complete_task(task, verdict, frames, "vision model")
        return record

    def _remember(self, record: AnalysisRecord) -> None:
        self._last_record = record
        self._history.append(record)

    def _record(
        self,
        task: Task,
        verdict: Verdict | None,
        votes: int,
        confirmed: bool,
        stream_ids: list[str],
        error: str | None = None,
    ) -> AnalysisRecord:
        self._seq += 1
        return AnalysisRecord(
            id=self._seq,
            timestamp=datetime.now(UTC),
            task_id=task.id,
            task_title=task.title,
            verdict=verdict,
            votes=votes,
            votes_required=self._tracker.votes_required,
            confirmed=confirmed,
            stream_ids=stream_ids,
            error=error,
        )

    # ------------------------------------------------------------ completion
    async def _complete_task(
        self,
        task: Task,
        verdict: Verdict | None,
        frames: list[Snapshot],
        reason: str,
    ) -> None:
        completed_at = datetime.now(UTC)
        self._state.mark_completed(task.id, completed_at)
        self._tracker.reset()
        self._last_completion = time.monotonic()

        pending = self._pending_tasks()
        self._current = pending[0] if pending else None
        next_task = self._current
        progress = self.progress()

        self._emit(
            "task_completed",
            {
                "task": task.model_dump(mode="json"),
                "next_task": next_task.model_dump(mode="json") if next_task else None,
                "reason": reason,
                "confidence": verdict.confidence if verdict else None,
                "completed_at": completed_at.isoformat(),
                "progress": progress,
            },
        )

        if next_task is not None:
            self._all_done_announced = False
            log.info(
                '✅ TASK COMPLETED: "%s"  →  next task: "%s"  (%d/%d done)',
                task.title,
                next_task.title,
                progress["completed"],
                progress["total"],
            )
            self._emit(
                "task_activated",
                {"task": next_task.model_dump(mode="json"), "progress": progress},
            )
        else:
            log.info(
                '✅ TASK COMPLETED: "%s"  →  no tasks remaining',
                task.title,
            )
            self._announce_all_done()

        if frames and self._config.analysis.save_evidence:
            try:
                await asyncio.to_thread(
                    self._save_evidence, task, verdict, frames, completed_at, reason
                )
            except Exception:  # pragma: no cover - disk issues must not stop us
                log.exception("failed to save evidence for task %s", task.id)

    def _save_evidence(
        self,
        task: Task,
        verdict: Verdict | None,
        frames: list[Snapshot],
        completed_at: datetime,
        reason: str,
    ) -> None:
        folder = (
            Path(self._config.app.data_dir)
            / "evidence"
            / (re.sub(r"[^A-Za-z0-9_.-]+", "_", task.id)[:120] or "task")
            / completed_at.strftime("%Y%m%dT%H%M%SZ")
        )
        folder.mkdir(parents=True, exist_ok=True)

        for frame in frames:
            (folder / f"{frame.stream_id}_{frame.seq:06d}.jpg").write_bytes(frame.jpeg)

        system_prompt, user_prompt = self._prompts.build(task, frames)
        (folder / "prompt.md").write_text(
            f"# System\n\n{system_prompt}\n\n# User\n\n{user_prompt}\n",
            encoding="utf-8",
        )
        payload = {
            "task": task.model_dump(mode="json"),
            "verdict": verdict.model_dump(mode="json") if verdict else None,
            "reason": reason,
            "completed_at": completed_at.isoformat(),
            "frames": [
                {
                    "stream_id": frame.stream_id,
                    "stream_name": frame.stream_name,
                    "seq": frame.seq,
                    "captured_at": frame.captured_at.isoformat(),
                    "width": frame.width,
                    "height": frame.height,
                }
                for frame in frames
            ],
        }
        (folder / "verdict.json").write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        log.info("evidence saved to %s", folder)

    # ---------------------------------------------------------------- state
    def progress(self) -> dict[str, int]:
        task_ids = {task.id for task in self._tasks}
        completed = len(task_ids & self._state.completed_ids)
        return {
            "completed": completed,
            "total": len(task_ids),
            "remaining": len(task_ids) - completed,
        }

    @property
    def current_task(self) -> Task | None:
        return self._current

    @property
    def next_task(self) -> Task | None:
        pending = self._pending_tasks()
        if not pending:
            return None
        if self._current is not None and pending[0].id == self._current.id:
            return pending[1] if len(pending) > 1 else None
        return pending[0]

    def state(self) -> dict[str, Any]:
        completed = self._state.as_dict()
        rows = []
        for task in self._tasks:
            if task.id in completed:
                status = "completed"
            elif self._current is not None and task.id == self._current.id:
                status = "active"
            else:
                status = "pending"
            rows.append(
                {
                    "task": task.model_dump(mode="json"),
                    "status": status,
                    "completed_at": completed.get(task.id),
                }
            )

        uptime = (
            (datetime.now(UTC) - self._started_at).total_seconds()
            if self._started_at
            else 0.0
        )
        return {
            "running": self._running,
            "started_at": self._started_at.isoformat() if self._started_at else None,
            "uptime_seconds": round(uptime, 1),
            "task_source": self._tasks_source.name,
            "project": self._project,
            "model": {
                "provider": self._config.vision.provider,
                "name": self._vision.name,
                "base_url": self._config.vision.base_url,
            },
            "current_task": (
                self._current.model_dump(mode="json") if self._current else None
            ),
            "next_task": (
                self.next_task.model_dump(mode="json") if self.next_task else None
            ),
            "votes": self._tracker.votes,
            "votes_required": self._tracker.votes_required,
            "min_confidence": self._tracker.min_confidence,
            "progress": self.progress(),
            "tasks": rows,
            "streams": [status.model_dump(mode="json") for status in self._streams.statuses()],
            "last_analysis": (
                self._last_record.model_dump(mode="json") if self._last_record else None
            ),
            "history": [
                record.model_dump(mode="json")
                for record in list(self._history)[-20:][::-1]
            ],
            "analysis_interval_seconds": self._config.analysis.interval_seconds,
            "config_errors": self._config.validate_runtime(),
        }

    # ----------------------------------------------------------------- loop
    async def _run_loop(self) -> None:
        analysis_cfg = self._config.analysis
        # Give capture threads a moment to deliver their first frame.
        if analysis_cfg.startup_grace_seconds > 0:
            await asyncio.sleep(analysis_cfg.startup_grace_seconds)
        while self._running:
            try:
                if (
                    time.monotonic() - self._last_refresh
                    >= self._config.tasks.refresh_seconds
                ):
                    await self.refresh_tasks(raise_on_error=False)

                if self._current is None:
                    await asyncio.sleep(min(analysis_cfg.interval_seconds, 5.0))
                    continue

                if (
                    time.monotonic() - self._last_completion
                    < analysis_cfg.settle_seconds
                ):
                    await asyncio.sleep(min(1.0, analysis_cfg.interval_seconds))
                    continue

                await self.analyze_now()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # pragma: no cover - loop safety net
                log.exception("analysis loop error")
                self._emit(
                    "engine_error", {"error": f"{type(exc).__name__}: {exc}"}
                )
            await asyncio.sleep(analysis_cfg.interval_seconds)

    # --------------------------------------------------------------- events
    def _emit(self, event_type: str, data: dict[str, Any]) -> None:
        event = self._bus.publish(event_type, data)
        if self._event_log is not None:
            self._event_log.write(event)
