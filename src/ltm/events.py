"""In-process pub/sub used to push live updates to the dashboard."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from collections import deque
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

log = logging.getLogger("ltm.events")


@dataclass(slots=True)
class Event:
    seq: int
    ts: datetime
    type: str
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "ts": self.ts.isoformat(),
            "type": self.type,
            "data": self.data,
        }


class EventBus:
    """Fan-out bus with a short replay buffer for late subscribers."""

    def __init__(self, history_size: int = 100) -> None:
        self._history: deque[Event] = deque(maxlen=history_size)
        self._subscribers: set[asyncio.Queue[Event]] = set()
        self._seq = 0

    def publish(self, event_type: str, data: dict[str, Any] | None = None) -> Event:
        self._seq += 1
        event = Event(
            seq=self._seq,
            ts=datetime.now(UTC),
            type=event_type,
            data=data or {},
        )
        self._history.append(event)
        for queue in list(self._subscribers):
            # A slow browser must never stall the analysis loop.
            with suppress(asyncio.QueueFull):
                queue.put_nowait(event)
        return event

    def recent(self, limit: int = 50) -> list[Event]:
        return list(self._history)[-limit:]

    def subscribe(self, maxsize: int = 200) -> asyncio.Queue[Event]:
        queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=maxsize)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[Event]) -> None:
        self._subscribers.discard(queue)


class EventLog:
    """Append-only JSONL log so decisions survive a restart."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            log.warning("cannot create event log directory %s", self.path.parent)

    def write(self, event: Event) -> None:
        line = json.dumps(event.to_dict(), ensure_ascii=False, default=str)
        try:
            with self._lock, self.path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except OSError:
            log.debug("failed to append event log %s", self.path)
