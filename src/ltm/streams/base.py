"""Stream worker interface plus JPEG encoding helpers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import cv2

from ..config import StreamConfig
from ..schemas import StreamStatus


@dataclass(slots=True)
class Frame:
    stream_id: str
    stream_name: str
    image: Any  # numpy.ndarray, BGR
    captured_at: datetime
    seq: int


def encode_jpeg(
    image: Any, max_side: int = 768, quality: int = 80
) -> tuple[bytes, int, int]:
    """Downscale to ``max_side`` and encode as JPEG. Returns (bytes, width, height)."""
    height, width = image.shape[:2]
    longest = max(height, width)
    if longest > max_side:
        scale = max_side / float(longest)
        image = cv2.resize(
            image,
            (max(1, int(width * scale)), max(1, int(height * scale))),
            interpolation=cv2.INTER_AREA,
        )
    ok, buffer = cv2.imencode(
        ".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)]
    )
    if not ok:
        raise RuntimeError("OpenCV failed to encode a JPEG frame")
    height, width = image.shape[:2]
    return buffer.tobytes(), width, height


class StreamWorker(ABC):
    """Owns one camera feed and exposes its most recent frames."""

    def __init__(self, config: StreamConfig) -> None:
        self.config = config

    @property
    def id(self) -> str:
        return self.config.id

    @property
    def name(self) -> str:
        return self.config.name or self.config.id

    @abstractmethod
    def start(self) -> None: ...

    @abstractmethod
    def stop(self) -> None: ...

    @abstractmethod
    def recent(self, count: int = 1) -> list[Frame]:
        """Return up to ``count`` most recent frames, oldest first."""

    @abstractmethod
    def status(self) -> StreamStatus: ...

    def latest(self) -> Frame | None:
        frames = self.recent(1)
        return frames[-1] if frames else None
