"""Shared data models used by the engine, API and dashboard."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Task(BaseModel):
    """One unit of work, as returned by a task source."""

    model_config = ConfigDict(extra="ignore")

    id: str
    title: str
    description: str = ""
    instructions: str = ""
    order: float = 0
    action_type: str | None = None
    expected_time_sec: float | None = None
    success_message: str | None = None
    wip: bool = False
    project_id: str | None = None
    action_block_id: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict, exclude=True)


class Verdict(BaseModel):
    """A vision model's judgement for one analysis window."""

    task_completed: bool = False
    confidence: float = 0.0
    reasoning: str = ""
    evidence: list[str] = Field(default_factory=list)
    model: str | None = None
    latency_ms: int | None = None
    parse_error: bool = False
    raw_response: str = Field(default="", exclude=True)

    @field_validator("confidence")
    @classmethod
    def _clamp(cls, value: float) -> float:
        return max(0.0, min(1.0, float(value)))


class AnalysisRecord(BaseModel):
    """One completed analysis cycle, whether or not it produced a verdict."""

    id: int
    timestamp: datetime
    task_id: str
    task_title: str
    verdict: Verdict | None = None
    votes: int = 0
    votes_required: int = 1
    confirmed: bool = False
    stream_ids: list[str] = Field(default_factory=list)
    error: str | None = None


class StreamStatus(BaseModel):
    id: str
    name: str
    type: Literal["video", "push"]
    enabled: bool = True
    connected: bool = False
    frames: int = 0
    fps: float = 0.0
    last_frame_at: datetime | None = None
    error: str | None = None


class Snapshot(BaseModel):
    """A JPEG-encoded frame ready to send to the vision model."""

    stream_id: str
    stream_name: str
    captured_at: datetime
    seq: int
    jpeg: bytes = Field(exclude=True)
    width: int
    height: int
