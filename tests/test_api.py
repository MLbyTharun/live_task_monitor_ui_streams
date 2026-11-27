from __future__ import annotations

import json

import cv2
import numpy as np
from fastapi.testclient import TestClient

from ltm.api import create_app
from ltm.config import Config


def jpeg_bytes() -> bytes:
    image = np.zeros((120, 160, 3), dtype=np.uint8)
    ok, buffer = cv2.imencode(".jpg", image)
    assert ok
    return buffer.tobytes()


def build_app(tmp_path):
    prompts = tmp_path / "prompts"
    prompts.mkdir(exist_ok=True)
    (prompts / "system.md").write_text("system", encoding="utf-8")
    (prompts / "verify.md").write_text(
        "Task: {{task_title}}\nReturn JSON.", encoding="utf-8"
    )
    tasks = tmp_path / "tasks.json"
    tasks.write_text(
        json.dumps(
            [
                {"id": "t1", "title": "First", "order": 1},
                {"id": "t2", "title": "Second", "order": 2},
            ]
        ),
        encoding="utf-8",
    )
    config = Config.model_validate(
        {
            "app": {"data_dir": str(tmp_path / "data")},
            "tasks": {"source": "static", "static_file": str(tasks)},
            "vision": {"provider": "mock", "mock_complete_after": 99},
            "analysis": {
                "auto_start": False,
                "save_evidence": False,
                "confirmation_votes": 2,
            },
            "prompts": {
                "system_file": str(prompts / "system.md"),
                "verify_file": str(prompts / "verify.md"),
            },
            "streams": [
                {"id": "phone", "name": "Phone", "type": "push", "enabled": True},
                {"id": "cam1", "name": "Camera", "type": "video", "url": "0", "enabled": False},
            ],
        }
    )
    config.root = tmp_path
    return create_app(config)


def test_push_ingest_analyze_and_manual_completion(tmp_path):
    app = build_app(tmp_path)
    with TestClient(app) as client:
        state = client.get("/api/state").json()
        assert state["current_task"]["id"] == "t1"
        assert state["progress"] == {"completed": 0, "total": 2, "remaining": 2}

        # No frame yet -> snapshot 404
        assert client.get("/api/streams/phone/snapshot").status_code == 404

        response = client.post(
            "/api/ingest/phone",
            content=jpeg_bytes(),
            headers={"Content-Type": "image/jpeg"},
        )
        assert response.status_code == 200
        assert response.json()["seq"] == 1

        snapshot = client.get("/api/streams/phone/snapshot")
        assert snapshot.status_code == 200
        assert snapshot.headers["content-type"] == "image/jpeg"

        analysis = client.post("/api/analyze").json()
        record = analysis["record"]
        assert record["task_id"] == "t1"
        assert record["verdict"]["task_completed"] is False
        assert record["votes"] == 0

        completed = client.post("/api/tasks/t1/complete")
        assert completed.status_code == 200
        assert completed.json()["current_task"]["id"] == "t2"

        reset = client.post("/api/state/reset").json()
        assert reset["current_task"]["id"] == "t1"
        assert reset["progress"]["completed"] == 0

        events = client.get("/api/events/recent").json()["events"]
        types = [event["type"] for event in events]
        assert "task_completed" in types
        assert "analysis" in types


def test_ingest_rejects_wrong_stream_and_bad_body(tmp_path):
    app = build_app(tmp_path)
    with TestClient(app) as client:
        assert client.post("/api/ingest/nope", content=b"x").status_code == 404
        assert client.post("/api/ingest/cam1", content=b"x").status_code == 400
        bad = client.post(
            "/api/ingest/phone",
            content=b"not an image",
            headers={"Content-Type": "image/jpeg"},
        )
        assert bad.status_code == 400


def test_health_and_config_endpoints(tmp_path):
    app = build_app(tmp_path)
    with TestClient(app) as client:
        health = client.get("/api/health").json()
        assert health["status"] == "ok"
        assert health["streams"]["total"] == 2

        public = client.get("/api/config").json()
        assert public["vision"]["provider"] == "mock"
        assert "api_key" not in public["vision"]
        assert "token" not in public["vision"]


def test_project_selection_scopes_progress_and_cameras(tmp_path):
    app = build_app(tmp_path)
    with TestClient(app) as client:
        state = client.get("/api/state").json()
        assert state["project"]["id"] == "local"

        projects = client.get("/api/projects").json()
        assert projects["source"] == "static"
        assert projects["projects"] == [{"id": "local", "name": "Local task list"}]

        # Local progress is independent from the next project's progress.
        client.post("/api/tasks/t1/complete")
        assert client.get("/api/state").json()["progress"]["completed"] == 1

        switched = client.post(
            "/api/project", json={"id": "project-b", "name": "Project B"}
        )
        assert switched.status_code == 200
        body = switched.json()
        assert body["project"] == {"id": "project-b", "name": "Project B"}
        assert body["progress"] == {"completed": 0, "total": 2, "remaining": 2}

        # Each project gets its own camera list, seeded from config.
        cameras = client.get("/api/cameras").json()
        assert cameras["project"]["id"] == "project-b"
        assert {camera["id"] for camera in cameras["cameras"]} == {"phone", "cam1"}

        client.post("/api/project", json={"id": "local"})
        assert client.get("/api/state").json()["progress"]["completed"] == 1

    # The selected project survives a restart.
    with TestClient(build_app(tmp_path)) as restarted:
        assert restarted.get("/api/state").json()["project"]["id"] == "local"


def test_camera_crud_and_live_ingest(tmp_path):
    app = build_app(tmp_path)
    with TestClient(app) as client:
        saved = client.post(
            "/api/cameras",
            json={
                "id": "tablet",
                "name": "Tablet camera",
                "type": "push",
                "enabled": True,
            },
        )
        assert saved.status_code == 200
        assert any(
            camera["id"] == "tablet" for camera in saved.json()["cameras"]
        )

        ingest = client.post(
            "/api/ingest/tablet",
            content=jpeg_bytes(),
            headers={"Content-Type": "image/jpeg"},
        )
        assert ingest.status_code == 200

        edited = client.put(
            "/api/cameras/tablet",
            json={
                "name": "Tablet (paused)",
                "type": "push",
                "enabled": False,
            },
        )
        assert edited.status_code == 200
        tablet = next(
            camera
            for camera in edited.json()["cameras"]
            if camera["id"] == "tablet"
        )
        assert tablet["name"] == "Tablet (paused)"
        assert tablet["enabled"] is False

        bad = client.post(
            "/api/cameras", json={"id": "broken", "type": "video"}
        )
        assert bad.status_code == 400

        removed = client.delete("/api/cameras/tablet")
        assert removed.status_code == 200
        assert all(
            camera["id"] != "tablet" for camera in removed.json()["cameras"]
        )
        assert client.post("/api/ingest/tablet", content=jpeg_bytes()).status_code == 404
        assert client.delete("/api/cameras/nope").status_code == 404
