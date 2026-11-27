"""OpenAI-compatible vision client (vLLM, Ollama, OpenRouter, DeepInfra, ...)."""

from __future__ import annotations

import base64
import logging
import time
from typing import Any

import httpx

from ..config import VisionConfig
from ..schemas import Snapshot, Task, Verdict
from .base import VisionError, VisionModel
from .parsing import parse_verdict

log = logging.getLogger("ltm.vision.openai")

_MAX_ERROR_CHARS = 500


class OpenAICompatibleVision(VisionModel):
    """Sends frames as data URLs to ``POST {base_url}/chat/completions``."""

    def __init__(self, config: VisionConfig) -> None:
        if not config.base_url.strip():
            raise VisionError("VLM_BASE_URL is empty — set it in .env")
        if not config.model.strip():
            raise VisionError("VLM_MODEL is empty — set it in .env")

        self.config = config
        self.name = config.model
        headers = {"Accept": "application/json", **config.extra_headers}
        if config.api_key.strip():
            headers["Authorization"] = f"Bearer {config.api_key.strip()}"

        self._client = httpx.AsyncClient(
            base_url=config.base_url.rstrip("/") + "/",
            headers=headers,
            timeout=httpx.Timeout(config.timeout_seconds, connect=15.0),
        )

    # ---------------------------------------------------------------- model
    async def analyze(
        self,
        task: Task,
        frames: list[Snapshot],
        system_prompt: str,
        user_prompt: str,
    ) -> Verdict:
        if not frames:
            raise VisionError("no frames supplied to the vision model")

        content: list[dict[str, Any]] = [{"type": "text", "text": user_prompt}]
        for index, frame in enumerate(frames, start=1):
            content.append(
                {
                    "type": "text",
                    "text": (
                        f"Frame {index} — camera '{frame.stream_name}' "
                        f"({frame.stream_id}) at {frame.captured_at.isoformat(timespec='seconds')}"
                    ),
                }
            )
            encoded = base64.b64encode(frame.jpeg).decode("ascii")
            image: dict[str, Any] = {
                "url": f"data:image/jpeg;base64,{encoded}",
            }
            if self.config.image_detail in ("low", "high"):
                image["detail"] = self.config.image_detail
            content.append({"type": "image_url", "image_url": image})

        body: dict[str, Any] = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content},
            ],
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
        }
        if self.config.json_mode:
            body["response_format"] = {"type": "json_object"}
        body.update(self.config.extra_body)

        started = time.perf_counter()
        try:
            response = await self._client.post("chat/completions", json=body)
        except httpx.TimeoutException as exc:
            raise VisionError(
                f"vision model timed out after {self.config.timeout_seconds:g}s"
            ) from exc
        except httpx.HTTPError as exc:
            raise VisionError(f"cannot reach vision model: {exc}") from exc

        latency_ms = int((time.perf_counter() - started) * 1000)

        if response.status_code >= 400:
            snippet = response.text[:_MAX_ERROR_CHARS].replace("\n", " ")
            hint = ""
            if response.status_code in (401, 403):
                hint = " — check VLM_API_KEY"
            elif response.status_code == 404:
                hint = " — check VLM_BASE_URL (it should end in /v1) and VLM_MODEL"
            raise VisionError(
                f"vision model returned HTTP {response.status_code}{hint}: {snippet}"
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise VisionError("vision model returned non-JSON content") from exc

        text = self._extract_text(payload)
        verdict = parse_verdict(text, model=self.config.model, latency_ms=latency_ms)
        if verdict.parse_error:
            log.warning("unparseable model output: %s", text[:300])
        return verdict

    @staticmethod
    def _extract_text(payload: dict[str, Any]) -> str:
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise VisionError(
                "vision model response has no choices: " + str(payload)[:_MAX_ERROR_CHARS]
            )
        message = choices[0].get("message") or {}
        content = message.get("content")
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            text = "".join(
                part.get("text", "")
                for part in content
                if isinstance(part, dict)
            )
        else:
            text = str(content or "")

        # Some reasoning models put the answer in reasoning_content instead.
        if not text.strip():
            text = str(message.get("reasoning_content") or "")
        return text

    # ---------------------------------------------------------------- admin
    async def ping(self) -> str:
        try:
            response = await self._client.get("models")
        except httpx.HTTPError as exc:
            raise VisionError(f"cannot reach {self.config.base_url}: {exc}") from exc
        if response.status_code >= 400:
            raise VisionError(
                f"GET {self.config.base_url.rstrip('/')}/models -> HTTP "
                f"{response.status_code}: {response.text[:200]}"
            )
        try:
            payload = response.json()
            ids = [item.get("id") for item in payload.get("data", []) if isinstance(item, dict)]
        except (ValueError, AttributeError):
            ids = []

        if ids and self.config.model not in ids:
            return (
                f"endpoint reachable, but '{self.config.model}' is not in the model "
                f"list ({', '.join(str(i) for i in ids[:5])})"
            )
        return f"endpoint reachable, model '{self.config.model}'"

    async def close(self) -> None:
        await self._client.aclose()
