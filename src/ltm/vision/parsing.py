"""Lenient parsing of the JSON verdict returned by a vision model."""

from __future__ import annotations

import json
import re
from typing import Any

from ..schemas import Verdict

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)
_TRUE = {"true", "yes", "y", "1", "completed", "complete", "done"}
_FALSE = {"false", "no", "n", "0", "incomplete", "not completed", "not_complete"}


def _first_json_object(text: str) -> dict[str, Any] | None:
    """Scan for the first balanced ``{...}`` block and decode it."""
    start = text.find("{")
    while start != -1:
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(text)):
            char = text[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
            elif char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    try:
                        value = json.loads(text[start : index + 1])
                    except ValueError:
                        break
                    if isinstance(value, dict):
                        return value
                    break
        start = text.find("{", start + 1)
    return None


def _extract_json(text: str) -> dict[str, Any] | None:
    for block in _FENCE_RE.findall(text):
        block = block.strip()
        try:
            value = json.loads(block)
        except ValueError:
            value = _first_json_object(block)
        if isinstance(value, dict):
            return value

    stripped = text.strip()
    try:
        value = json.loads(stripped)
        if isinstance(value, dict):
            return value
    except ValueError:
        pass
    return _first_json_object(stripped)


def _as_bool(value: Any) -> tuple[bool, bool]:
    """Return (value, was_understood)."""
    if isinstance(value, bool):
        return value, True
    if isinstance(value, (int, float)):
        return bool(value), True
    if isinstance(value, str):
        cleaned = value.strip().lower()
        if cleaned in _TRUE:
            return True, True
        if cleaned in _FALSE:
            return False, True
    return False, False


def _as_confidence(value: Any) -> float:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    try:
        number = float(str(value).strip().rstrip("%")) if value is not None else 0.0
    except (TypeError, ValueError):
        return 0.0
    if "%" in str(value) or 1.0 < number <= 100.0:
        number /= 100.0
    return max(0.0, min(1.0, number))


def parse_verdict(
    text: str,
    *,
    model: str | None = None,
    latency_ms: int | None = None,
) -> Verdict:
    """Turn raw model text into a :class:`Verdict`, never raising."""
    raw = text or ""
    payload = _extract_json(raw)
    if payload is None:
        return Verdict(
            task_completed=False,
            confidence=0.0,
            reasoning=f"Model output was not valid JSON: {raw[:300]}",
            parse_error=True,
            model=model,
            latency_ms=latency_ms,
            raw_response=raw,
        )

    completed_key = next(
        (key for key in ("task_completed", "completed", "done", "is_completed") if key in payload),
        None,
    )
    if completed_key is None:
        return Verdict(
            task_completed=False,
            confidence=0.0,
            reasoning="Model JSON did not contain a task_completed field",
            parse_error=True,
            model=model,
            latency_ms=latency_ms,
            raw_response=raw,
        )

    completed, understood = _as_bool(payload.get(completed_key))
    confidence = _as_confidence(payload.get("confidence", payload.get("score", 0.0)))
    reasoning = payload.get("reasoning") or payload.get("explanation") or payload.get("reason") or ""
    evidence = payload.get("evidence") or payload.get("observations") or []
    if isinstance(evidence, str):
        evidence = [evidence]
    if not isinstance(evidence, list):
        evidence = []
    evidence = [str(item).strip() for item in evidence if str(item).strip()][:10]

    return Verdict(
        task_completed=completed,
        confidence=confidence,
        reasoning=str(reasoning).strip(),
        evidence=evidence,
        parse_error=not understood,
        model=model,
        latency_ms=latency_ms,
        raw_response=raw,
    )
