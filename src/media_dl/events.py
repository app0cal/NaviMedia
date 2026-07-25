"""Publish lightweight in-process events for live dashboard refreshes."""

from __future__ import annotations

import json
import queue
from contextlib import contextmanager
from dataclasses import dataclass
from threading import Lock
from typing import Iterator


@dataclass(frozen=True)
class Event:
    """Describe one changed backend record."""

    name: str
    data: dict[str, object]


class EventBroker:
    """Fan out non-blocking notifications to connected SSE clients."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._subscribers: set[queue.Queue[Event]] = set()

    def publish(self, name: str, **data: object) -> None:
        """Offer an event to every subscriber without blocking worker work."""
        event = Event(name=name, data=data)
        with self._lock:
            subscribers = tuple(self._subscribers)
        for subscriber in subscribers:
            try:
                subscriber.put_nowait(event)
            except queue.Full:
                # A slow browser will recover through the authoritative REST refresh.
                pass

    @contextmanager
    def subscribe(self) -> Iterator[queue.Queue[Event]]:
        """Register a bounded subscriber queue for one SSE connection."""
        subscriber: queue.Queue[Event] = queue.Queue(maxsize=100)
        with self._lock:
            self._subscribers.add(subscriber)
        try:
            yield subscriber
        finally:
            with self._lock:
                self._subscribers.discard(subscriber)

    def stream(self) -> Iterator[str]:
        """Yield SSE frames and periodic heartbeat comments."""
        with self.subscribe() as subscriber:
            yield "event: connected\ndata: {}\n\n"
            while True:
                try:
                    event = subscriber.get(timeout=15)
                except queue.Empty:
                    yield ": heartbeat\n\n"
                    continue
                payload = json.dumps(event.data, separators=(",", ":"))
                yield f"event: {event.name}\ndata: {payload}\n\n"
