"""Builds and coordinates stream workers, with live reconfiguration."""

from __future__ import annotations

import logging
import threading

from ..config import StreamConfig
from ..schemas import Snapshot, StreamStatus
from .base import StreamWorker, encode_jpeg
from .push import PushStreamWorker
from .video import VideoStreamWorker

log = logging.getLogger("ltm.streams")


def _build_worker(config: StreamConfig) -> StreamWorker:
    if config.type == "push":
        return PushStreamWorker(config)
    return VideoStreamWorker(config)


class StreamManager:
    """Owns one worker per camera and can add/remove them without a restart."""

    def __init__(self, configs: list[StreamConfig]) -> None:
        self._lock = threading.RLock()
        self._workers: dict[str, StreamWorker] = {}
        self._started: set[str] = set()
        self.apply(configs, start=False)

    # ---------------------------------------------------------------- life
    def apply(self, configs: list[StreamConfig], start: bool = True) -> None:
        """Reconcile workers with ``configs``.

        Workers whose configuration is unchanged keep running (and keep their
        frame buffers). Changed or new cameras get a fresh worker; removed
        cameras are stopped. Set ``start=False`` to build without starting
        (used during app construction).
        """
        by_id: dict[str, StreamConfig] = {}
        for config in configs:
            if config.id in by_id:
                log.warning("ignoring duplicate stream id %s", config.id)
                continue
            by_id[config.id] = config

        with self._lock:
            for stream_id in list(self._workers):
                if stream_id not in by_id:
                    self._stop_locked(stream_id)

            for stream_id, config in by_id.items():
                existing = self._workers.get(stream_id)
                if (
                    existing is not None
                    and existing.config.model_dump() == config.model_dump()
                ):
                    if start:
                        if config.enabled:
                            self._start_locked(stream_id)
                        else:
                            self._stop_locked(stream_id, keep=True)
                    continue

                if existing is not None:
                    self._stop_locked(stream_id)
                self._workers[stream_id] = _build_worker(config)
                if start and config.enabled:
                    self._start_locked(stream_id)

            # Keep dashboard tiles in the same order the UI/config declares.
            self._workers = {
                stream_id: self._workers[stream_id]
                for stream_id in by_id
                if stream_id in self._workers
            }

    def start_all(self) -> None:
        with self._lock:
            for stream_id, worker in self._workers.items():
                if worker.config.enabled:
                    self._start_locked(stream_id)

    def stop_all(self) -> None:
        with self._lock:
            for stream_id in list(self._workers):
                self._stop_locked(stream_id, keep=True)

    def _start_locked(self, stream_id: str) -> None:
        worker = self._workers.get(stream_id)
        if worker is None or stream_id in self._started:
            return
        try:
            worker.start()
            self._started.add(stream_id)
        except Exception:  # pragma: no cover - defensive
            log.exception("failed to start stream %s", stream_id)

    def _stop_locked(self, stream_id: str, keep: bool = False) -> None:
        worker = self._workers.get(stream_id)
        if worker is None:
            self._started.discard(stream_id)
            if not keep:
                self._workers.pop(stream_id, None)
            return
        if stream_id in self._started:
            try:
                worker.stop()
            except Exception:  # pragma: no cover - defensive
                log.exception("failed to stop stream %s", stream_id)
        self._started.discard(stream_id)
        if not keep:
            self._workers.pop(stream_id, None)

    # --------------------------------------------------------------- access
    def get(self, stream_id: str) -> StreamWorker | None:
        with self._lock:
            return self._workers.get(stream_id)

    def workers(self) -> list[StreamWorker]:
        with self._lock:
            return list(self._workers.values())

    def configs(self) -> list[StreamConfig]:
        with self._lock:
            return [worker.config for worker in self._workers.values()]

    def statuses(self) -> list[StreamStatus]:
        with self._lock:
            workers = list(self._workers.values())
        return [worker.status() for worker in workers]

    # -------------------------------------------------------------- frames
    def snapshot(
        self,
        per_stream: int = 1,
        max_side: int = 768,
        quality: int = 80,
        max_total: int = 8,
    ) -> list[Snapshot]:
        """Collect recent frames from every enabled stream, newest first."""
        snapshots: list[Snapshot] = []
        for worker in self.workers():
            if not worker.config.enabled:
                continue
            for frame in worker.recent(per_stream):
                try:
                    jpeg, width, height = encode_jpeg(
                        frame.image, max_side=max_side, quality=quality
                    )
                except Exception as exc:
                    log.warning("cannot encode frame from %s: %s", worker.id, exc)
                    continue
                snapshots.append(
                    Snapshot(
                        stream_id=frame.stream_id,
                        stream_name=frame.stream_name,
                        captured_at=frame.captured_at,
                        seq=frame.seq,
                        jpeg=jpeg,
                        width=width,
                        height=height,
                    )
                )
        snapshots.sort(key=lambda item: item.captured_at, reverse=True)
        return snapshots[:max_total]
