from __future__ import annotations

import json

import httpx
import pytest

from ltm.config import HyperRealityConfig, TasksConfig
from ltm.tasks.base import TaskSourceError
from ltm.tasks.hyperreality import HyperRealityTaskSource
from ltm.tasks.static import StaticTaskSource


def hyperreality_config(**overrides) -> HyperRealityConfig:
    values = {
        "base_url": "https://example.test",
        "organization_id": "org-1",
        "project_id": "project-1",
        "action_block_id": "block-1",
        "token": "token-1",
        "page_size": 2,
    }
    values.update(overrides)
    return HyperRealityConfig(**values)


def test_hyperreality_fetches_pages_filters_and_sorts():
    pages = {
        "1": {
            "data": [
                {
                    "_id": "b",
                    "title": "Second",
                    "order": 2,
                    "projectId": "project-1",
                    "actionBlockId": "block-1",
                    "description": "desc",
                    "textInstructions": "do the thing",
                },
                {
                    "_id": "wip",
                    "title": "Work in progress",
                    "order": 1,
                    "wip": True,
                    "projectId": "project-1",
                    "actionBlockId": "block-1",
                },
                {
                    "_id": "other",
                    "title": "Other project",
                    "order": 1,
                    "projectId": "project-2",
                    "actionBlockId": "block-1",
                },
            ],
            "meta": {"totalPages": 2},
        },
        "2": {
            "data": [
                {
                    "_id": "a",
                    "title": "First",
                    "order": 1,
                    "projectId": "project-1",
                    "actionBlockId": "block-1",
                    "expectedTimeSec": 30,
                    "successMessage": "done",
                }
            ],
            "meta": {"totalPages": 2},
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/authoringv2/tasks"
        assert request.headers["x-organization-id"] == "org-1"
        assert request.headers["authorization"] == "Bearer token-1"
        assert request.url.params["projectId"] == "project-1"
        return httpx.Response(200, json=pages[request.url.params["page"]])

    source = HyperRealityTaskSource(
        hyperreality_config(), transport=httpx.MockTransport(handler)
    )
    tasks = source.fetch_tasks()

    assert [task.id for task in tasks] == ["a", "b"]
    assert tasks[0].title == "First"
    assert tasks[0].expected_time_sec == 30
    assert tasks[0].success_message == "done"
    assert tasks[1].instructions == "do the thing"


def test_hyperreality_401_has_actionable_message():
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "nope"})

    source = HyperRealityTaskSource(
        hyperreality_config(), transport=httpx.MockTransport(handler)
    )
    with pytest.raises(TaskSourceError, match="HR_TOKEN"):
        source.fetch_tasks()


def test_hyperreality_requires_token():
    source = HyperRealityTaskSource(hyperreality_config(token=""))
    with pytest.raises(TaskSourceError, match="HR_TOKEN"):
        source.fetch_tasks()


def test_static_source(tmp_path):
    path = tmp_path / "tasks.json"
    path.write_text(
        json.dumps(
            [
                {"id": "t2", "title": "Two", "order": 2},
                {"id": "t1", "title": "One", "order": 1, "instructions": "go"},
                {"id": "skip", "title": "Skip", "wip": True},
            ]
        ),
        encoding="utf-8",
    )
    source = StaticTaskSource(
        TasksConfig(source="static", static_file=path)
    )
    tasks = source.fetch_tasks()
    assert [task.id for task in tasks] == ["t1", "t2"]
    assert tasks[0].instructions == "go"
