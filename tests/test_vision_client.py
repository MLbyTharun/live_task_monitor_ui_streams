from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

import httpx
import pytest

from ltm.config import VisionConfig
from ltm.schemas import Snapshot, Task
from ltm.vision.base import VisionError
from ltm.vision.openai_compat import OpenAICompatibleVision


def make_vision(handler) -> OpenAICompatibleVision:
    vision = OpenAICompatibleVision(
        VisionConfig(
            provider="openai_compatible",
            base_url="http://model.test/v1",
            api_key="secret",
            model="qwen3-vl-2b",
            max_tokens=200,
        )
    )
    vision._client = httpx.AsyncClient(
        base_url="http://model.test/v1/",
        headers={"Authorization": "Bearer secret"},
        transport=httpx.MockTransport(handler),
    )
    return vision


def make_frame() -> Snapshot:
    return Snapshot(
        stream_id="cam1",
        stream_name="Camera 1",
        captured_at=datetime.now(UTC),
        seq=7,
        jpeg=b"fake-jpeg",
        width=640,
        height=360,
    )


def test_sends_multimodal_payload_and_parses_verdict():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["auth"] = request.headers.get("authorization")
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": '{"task_completed": true, "confidence": 0.91, '
                            '"reasoning": "block is in the bin", "evidence": ["bin"]}'
                        }
                    }
                ]
            },
        )

    vision = make_vision(handler)
    task = Task(id="t1", title="Place block", order=1)

    verdict = asyncio.run(
        vision.analyze(task, [make_frame()], "system prompt", "user prompt")
    )
    asyncio.run(vision.close())

    assert captured["path"] == "/v1/chat/completions"
    assert captured["auth"] == "Bearer secret"
    body = captured["body"]
    assert body["model"] == "qwen3-vl-2b"
    assert body["response_format"] == {"type": "json_object"}
    assert body["messages"][0] == {"role": "system", "content": "system prompt"}

    content = body["messages"][1]["content"]
    assert content[0] == {"type": "text", "text": "user prompt"}
    image_parts = [part for part in content if part["type"] == "image_url"]
    assert len(image_parts) == 1
    assert image_parts[0]["image_url"]["url"].startswith("data:image/jpeg;base64,")

    assert verdict.task_completed is True
    assert verdict.confidence == pytest.approx(0.91)
    assert verdict.model == "qwen3-vl-2b"
    assert verdict.latency_ms is not None


def test_http_error_becomes_vision_error():
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "bad key"})

    vision = make_vision(handler)
    task = Task(id="t1", title="Place block", order=1)

    with pytest.raises(VisionError, match="VLM_API_KEY"):
        asyncio.run(vision.analyze(task, [make_frame()], "s", "u"))
    asyncio.run(vision.close())


def test_missing_endpoint_is_rejected_early():
    with pytest.raises(VisionError, match="VLM_BASE_URL"):
        OpenAICompatibleVision(VisionConfig(provider="openai_compatible", base_url="", model="m"))
    with pytest.raises(VisionError, match="VLM_MODEL"):
        OpenAICompatibleVision(
            VisionConfig(provider="openai_compatible", base_url="http://x/v1", model="")
        )


def test_list_content_is_joined():
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": [
                                {"type": "text", "text": '{"task_completed": false, '},
                                {"type": "text", "text": '"confidence": 0.1}'},
                            ]
                        }
                    }
                ]
            },
        )

    vision = make_vision(handler)
    verdict = asyncio.run(
        vision.analyze(Task(id="t", title="x"), [make_frame()], "s", "u")
    )
    asyncio.run(vision.close())

    assert verdict.task_completed is False
    assert verdict.confidence == pytest.approx(0.1)
