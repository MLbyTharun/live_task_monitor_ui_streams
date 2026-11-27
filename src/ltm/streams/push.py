"""Push-based stream: frames arrive over HTTP (browser camera, WebRTC bridge...)."""

from __future__ import annotations

import threading
import time
from collections import deque
from datetime import UTC, datetime

import cv2
import numpy as np

from ..config import StreamConfig
from ..schemas import StreamStatus
from .base import Frame, StreamWorker


class PushStreamWorker(StreamWorker):
    """Holds the latest frame POSTed to ``/api/ingest/{stream_id}``.

    A browser, a phone app, or a WebRTC-to-JPEG bridge can all feed the same
    stream without the monitor needing to know the transport.
    """

    def __init__(self, config: StreamConfig) -> None:
        super().__init__(config)
        self._lock = threading.Lock()
        self._frames: deque[Frame] = deque(maxlen=8)
        self._emit_times: deque[float] = deque(maxlen=60)
        self._seq = 0
        self._received = 0
        self._connected = False
        self._error: str | None = None

    def start(self) -> None:
        # Nothing to open — the producer connects to us.
        pass

    def stop(self) -> None:
        self._connected = False

    def submit(self, data: bytes) -> Frame:
        """Decode an uploaded JPEG/PNG and make it the current frame."""
        if not data:
            raise ValueError("empty image body")
        image = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError("body is not a decodable JPEG/PNG image")

        now = datetime.now(UTC)
        with self._lock:
            self._seq += 1
            frame = Frame(
                stream_id=self.id,
                stream_name=self.name,
                image=image,
                captured_at=now,
                seq=self._seq,
            )
            self._frames.append(frame)
            self._emit_times.append(time.monotonic())
            self._received += 1
            self._connected = True
            self._error = None
        return frame

    def recent(self, count: int = 1) -> list[Frame]:
        with self._lock:
            if count <= 0:
                return []
            return list(self._frames)[-count:]

    def status(self) -> StreamStatus:
        with self._lock:
            frames = list(self._frames)
            times = list(self._emit_times)
            received = self._received
            error = self._error
        last = frames[-1].captured_at if frames else None
        fps = 0.0
        if len(times) >= 2:
            span = times[-1] - times[0]
            if span > 0:
                fps = (len(times) - 1) / span
        fresh = (
            last is not None
            and (datetime.now(UTC) - last).total_seconds()
            <= self.config.push_timeout_seconds
        )
        return StreamStatus(
            id=self.id,
            name=self.name,
            type="push",
            enabled=self.config.enabled,
            connected=self._connected and fresh,
            frames=received,
            fps=round(fps, 1),
            last_frame_at=last,
            error=error,
        )
