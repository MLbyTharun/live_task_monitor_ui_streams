"""FastAPI app: JSON state, SSE events, snapshots, push ingest, dashboard."""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError

from .config import Config, StreamConfig
from .engine import TaskEngine
from .events import EventBus, EventLog
from .prompts import PromptLibrary
from .settings import SettingsStore
from .state import StateStore
from .streams import PushStreamWorker, StreamManager, encode_jpeg
from .tasks import TaskSourceError, build_task_source
from .tasks.hyperreality import HyperRealityTaskSource
from .vision import build_vision

log = logging.getLogger("ltm.api")

MAX_INGEST_BYTES = 10 * 1024 * 1024
DASHBOARD_DIR = Path(__file__).with_name("dashboard")


class ProjectSelection(BaseModel):
    id: str = Field(min_length=1)
    name: str | None = None


class CameraPayload(BaseModel):
    id: str | None = None
    name: str = ""
    type: Literal["video", "push"] = "video"
    url: str = ""
    enabled: bool = True
    fps_limit: float = Field(default=2, gt=0, le=60)
    loop_file: bool = True
    reconnect_seconds: float = Field(default=3, gt=0)
    push_timeout_seconds: float = Field(default=10, gt=0)


def _camera_from_payload(
    payload: CameraPayload, forced_id: str | None = None
) -> StreamConfig:
    raw_id = (forced_id or payload.id or payload.name or "camera").strip()
    camera_id = re.sub(r"[^A-Za-z0-9_.-]+", "-", raw_id).strip("-.")[:64]
    if not camera_id:
        raise HTTPException(status_code=400, detail="camera id is required")

    data = payload.model_dump()
    data["id"] = camera_id
    if data["type"] == "push":
        data["url"] = ""
    if (
        data["type"] == "video"
        and data["enabled"]
        and not str(data["url"]).strip()
    ):
        raise HTTPException(status_code=400, detail="an enabled video camera needs a URL")

    try:
        return StreamConfig.model_validate(data)
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc



def create_app(config: Config) -> FastAPI:
    bus = EventBus()
    prompts = PromptLibrary(config.prompts.system_file, config.prompts.verify_file)
    settings = SettingsStore(config.app.data_dir / config.app.settings_file)
    state = StateStore(config.app.data_dir / "state.json")
    event_log = EventLog(config.app.data_dir / "events.jsonl")
    streams = StreamManager(config.streams)
    vision = build_vision(config)
    task_source = build_task_source(config)

    engine = TaskEngine(
        config=config,
        task_source=task_source,
        vision=vision,
        streams=streams,
        prompts=prompts,
        bus=bus,
        state=state,
        event_log=event_log,
        settings=settings,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        await engine.start()
        try:
            yield
        finally:
            await engine.stop()

    app = FastAPI(
        title="Live Task Monitor",
        version="0.1.0",
        description="Multi-camera task completion detection with a vision model",
        lifespan=lifespan,
    )
    app.state.config = config
    app.state.engine = engine
    app.state.bus = bus
    app.state.settings = settings

    if config.app.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=config.app.cors_origins,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    # ------------------------------------------------------------- helpers
    def check_ingest_token(request: Request) -> None:
        expected = config.app.ingest_token
        if not expected:
            return
        supplied = (
            request.headers.get("x-ingest-token")
            or request.query_params.get("token")
            or ""
        )
        if not hmac.compare_digest(supplied, expected):
            raise HTTPException(status_code=401, detail="invalid ingest token")

    # -------------------------------------------------------------- routes
    @app.get("/api/health")
    async def health() -> dict:
        statuses = streams.statuses()
        return {
            "status": "ok",
            "running": engine.state()["running"],
            "model": vision.name,
            "task_source": task_source.name,
            "streams": {
                "total": len(statuses),
                "connected": sum(1 for status in statuses if status.connected),
            },
            "config_errors": config.validate_runtime(),
        }

    @app.get("/api/state")
    async def get_state() -> dict:
        return engine.state()

    @app.get("/api/config")
    async def get_config() -> dict:
        return config.public_dict()

    # ------------------------------------------------------ project/cameras
    @app.get("/api/projects")
    async def list_projects() -> dict:
        active = engine.project
        if config.tasks.source != "hyperreality":
            return {
                "source": "static",
                "projects": [{"id": "local", "name": "Local task list"}],
                "active_project": active,
                "error": None,
            }

        source = HyperRealityTaskSource(config.tasks.hyperreality)
        try:
            projects = await asyncio.to_thread(source.list_projects)
        except TaskSourceError as exc:
            return {
                "source": "hyperreality",
                "projects": [],
                "active_project": active,
                "error": str(exc),
            }
        return {
            "source": "hyperreality",
            "projects": projects,
            "active_project": active,
            "error": None,
        }

    @app.post("/api/project")
    async def select_project(payload: ProjectSelection) -> dict:
        try:
            await engine.set_project(payload.id, payload.name)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return engine.state()

    @app.get("/api/cameras")
    async def list_cameras() -> dict:
        return {
            "project": engine.project,
            "cameras": engine.camera_configs(),
        }

    @app.post("/api/cameras")
    async def save_camera(payload: CameraPayload) -> dict:
        camera = _camera_from_payload(payload)
        try:
            saved = await engine.upsert_camera(camera)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "camera": saved.model_dump(mode="json"),
            "cameras": engine.camera_configs(),
        }

    @app.put("/api/cameras/{camera_id}")
    async def update_camera(camera_id: str, payload: CameraPayload) -> dict:
        camera = _camera_from_payload(payload, forced_id=camera_id)
        try:
            saved = await engine.upsert_camera(camera)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "camera": saved.model_dump(mode="json"),
            "cameras": engine.camera_configs(),
        }

    @app.delete("/api/cameras/{camera_id}")
    async def remove_camera(camera_id: str) -> dict:
        try:
            await engine.delete_camera(camera_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"cameras": engine.camera_configs()}

    @app.post("/api/cameras/test")
    async def test_camera(payload: CameraPayload) -> dict:
        camera = _camera_from_payload(payload)
        try:
            return await engine.test_camera(camera)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/tasks")
    async def get_tasks() -> dict:
        snapshot = engine.state()
        return {
            "tasks": snapshot["tasks"],
            "progress": snapshot["progress"],
            "current_task": snapshot["current_task"],
            "next_task": snapshot["next_task"],
        }

    @app.post("/api/tasks/refresh")
    async def refresh_tasks() -> dict:
        try:
            await engine.refresh_tasks()
        except TaskSourceError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return engine.state()

    @app.post("/api/tasks/{task_id}/complete")
    async def complete_task(task_id: str) -> dict:
        try:
            await engine.mark_completed(task_id, reason="manual (API)")
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return engine.state()

    @app.post("/api/state/reset")
    async def reset_state() -> dict:
        await engine.reset_progress()
        return engine.state()

    @app.post("/api/analyze")
    async def analyze_now() -> dict:
        record = await engine.analyze_now()
        return {"record": record.model_dump(mode="json") if record else None}

    @app.get("/api/streams")
    async def list_streams() -> dict:
        return {"streams": [status.model_dump(mode="json") for status in streams.statuses()]}

    @app.get("/api/streams/{stream_id}/snapshot")
    async def stream_snapshot(stream_id: str) -> Response:
        worker = streams.get(stream_id)
        if worker is None:
            raise HTTPException(status_code=404, detail="unknown stream")
        frame = worker.latest()
        if frame is None:
            raise HTTPException(status_code=404, detail="no frame received yet")
        jpeg, _, _ = encode_jpeg(frame.image, max_side=1280, quality=75)
        return Response(
            content=jpeg,
            media_type="image/jpeg",
            headers={"Cache-Control": "no-store"},
        )

    @app.post("/api/ingest/{stream_id}")
    async def ingest_frame(stream_id: str, request: Request) -> dict:
        worker = streams.get(stream_id)
        if worker is None:
            raise HTTPException(status_code=404, detail="unknown stream")
        if not isinstance(worker, PushStreamWorker):
            raise HTTPException(
                status_code=400,
                detail=f"stream '{stream_id}' is not a push stream (set type: push)",
            )
        check_ingest_token(request)

        body = await request.body()
        if not body:
            raise HTTPException(status_code=400, detail="empty image body")
        if len(body) > MAX_INGEST_BYTES:
            raise HTTPException(status_code=413, detail="image larger than 10 MB")
        try:
            frame = worker.submit(body)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "ok": True,
            "stream_id": stream_id,
            "seq": frame.seq,
            "bytes": len(body),
        }

    @app.get("/api/events/recent")
    async def recent_events(limit: int = 50) -> dict:
        limit = max(1, min(limit, 200))
        return {"events": [event.to_dict() for event in bus.recent(limit)]}

    @app.get("/api/events")
    async def events(request: Request) -> StreamingResponse:
        async def generator() -> AsyncIterator[str]:
            queue = bus.subscribe()
            try:
                yield "retry: 3000\n\n"
                for event in bus.recent(20):
                    yield f"id: {event.seq}\ndata: {json.dumps(event.to_dict(), default=str)}\n\n"
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=15)
                    except TimeoutError:
                        yield ": ping\n\n"
                        continue
                    yield (
                        f"id: {event.seq}\n"
                        f"data: {json.dumps(event.to_dict(), default=str)}\n\n"
                    )
            finally:
                bus.unsubscribe(queue)

        return StreamingResponse(
            generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # Dashboard is mounted last so /api routes always win.
    if DASHBOARD_DIR.is_dir():
        app.mount("/", StaticFiles(directory=DASHBOARD_DIR, html=True), name="dashboard")

    return app
