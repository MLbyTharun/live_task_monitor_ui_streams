"""RTSP / HLS / file / webcam capture in a background thread per stream."""

from __future__ import annotations

import logging
import math
import re
import threading
import time
from collections import deque
from datetime import UTC, datetime

import cv2

from ..config import StreamConfig
from ..schemas import StreamStatus
from .base import Frame, StreamWorker

log = logging.getLogger("ltm.streams.video")

_CREDENTIALS_RE = re.compile(r"(://)[^/@\s]+@")


def safe_url(url: str) -> str:
    """Hide user:password in logs and dashboard errors."""
    return _CREDENTIALS_RE.sub(r"\1***@", url)


class VideoStreamWorker(StreamWorker):
    """Continuously decodes a video source and keeps a small ring of frames.

    Network sources are drained at source rate (so the buffer never lags) but
    only ``fps_limit`` frames per second are retained for analysis.
    """

    def __init__(self, config: StreamConfig) -> None:
        super().__init__(config)
        url = config.url.strip()
        self._source: str | int = int(url) if url.isdigit() else url
        self._is_file = not url.isdigit() and "://" not in url
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._cap: cv2.VideoCapture | None = None
        self._frames: deque[Frame] = deque(maxlen=max(8, int(config.fps_limit * 4)))
        self._emit_times: deque[float] = deque(maxlen=60)
        self._seq = 0
        self._received = 0
        self._connected = False
        self._error: str | None = None

    # ---------------------------------------------------------------- life
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name=f"stream-{self.id}", daemon=True
        )
        self._thread.start()
        log.info("started stream %s (%s)", self.id, safe_url(self.config.url))

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            cap = self._cap
        if cap is not None:
            try:
                cap.release()
            except Exception as exc:
                log.debug("stream %s release failed: %s", self.id, exc)
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
        self._connected = False

    # --------------------------------------------------------------- frames
    def recent(self, count: int = 1) -> list[Frame]:
        with self._lock:
            if count <= 0:
                return []
            return list(self._frames)[-count:]

    def status(self) -> StreamStatus:
        with self._lock:
            frames = list(self._frames)
            times = list(self._emit_times)
        last = frames[-1].captured_at if frames else None
        fps = 0.0
        if len(times) >= 2:
            span = times[-1] - times[0]
            if span > 0:
                fps = (len(times) - 1) / span
        stale = (
            last is not None
            and (datetime.now(UTC) - last).total_seconds() > 15
        )
        return StreamStatus(
            id=self.id,
            name=self.name,
            type="video",
            enabled=self.config.enabled,
            connected=self._connected and not stale,
            frames=self._received,
            fps=round(fps, 1),
            last_frame_at=last,
            error=self._error,
        )

    # ----------------------------------------------------------------- loop
    def _run(self) -> None:
        while not self._stop.is_set():
            cap = self._open_capture()
            if cap is None or not cap.isOpened():
                self._set_error(f"cannot open {safe_url(self.config.url)}")
                self._stop.wait(self.config.reconnect_seconds)
                continue

            with self._lock:
                self._cap = cap
            self._connected = True
            self._error = None
            log.info("stream %s connected", self.id)

            source_fps = cap.get(cv2.CAP_PROP_FPS) or 0
            if not math.isfinite(source_fps) or source_fps <= 0:
                source_fps = 25.0
            frame_delay = 1.0 / source_fps
            last_store = 0.0

            try:
                while not self._stop.is_set():
                    started = time.monotonic()
                    ok, image = cap.read()
                    if not ok:
                        if self._is_file and self.config.loop_file:
                            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                            continue
                        self._set_error("stream ended or frame read failed")
                        break

                    self._received += 1
                    now = time.monotonic()
                    if now - last_store >= 1.0 / self.config.fps_limit:
                        last_store = now
                        self._store(image)

                    if self._is_file:
                        elapsed = time.monotonic() - started
                        if frame_delay > elapsed:
                            time.sleep(frame_delay - elapsed)
            except Exception as exc:
                self._set_error(f"{type(exc).__name__}: {exc}")
            finally:
                with self._lock:
                    if self._cap is cap:
                        self._cap = None
                cap.release()
                self._connected = False
                if not self._stop.is_set():
                    log.warning(
                        "stream %s disconnected (%s), reconnecting in %ss",
                        self.id,
                        self._error or "unknown reason",
                        self.config.reconnect_seconds,
                    )
                    self._stop.wait(self.config.reconnect_seconds)

        self._connected = False

    # -------------------------------------------------------------- helpers
    def _open_capture(self) -> cv2.VideoCapture | None:
        try:
            if self._is_file or isinstance(self._source, int):
                return cv2.VideoCapture(self._source)

            params = [
                cv2.CAP_PROP_OPEN_TIMEOUT_MSEC,
                10_000,
                cv2.CAP_PROP_READ_TIMEOUT_MSEC,
                10_000,
            ]
            try:
                cap = cv2.VideoCapture(self._source, cv2.CAP_FFMPEG, params)
            except TypeError:  # older OpenCV without the params overload
                cap = cv2.VideoCapture(self._source, cv2.CAP_FFMPEG)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            return cap
        except Exception as exc:
            log.warning("stream %s could not create capture: %s", self.id, exc)
            return None

    def _store(self, image) -> None:
        now = datetime.now(UTC)
        with self._lock:
            self._seq += 1
            self._frames.append(
                Frame(
                    stream_id=self.id,
                    stream_name=self.name,
                    image=image,
                    captured_at=now,
                    seq=self._seq,
                )
            )
            self._emit_times.append(time.monotonic())

    def _set_error(self, message: str) -> None:
        self._error = message
        log.warning("stream %s: %s", self.id, message)
