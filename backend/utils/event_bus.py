"""
Event Bus — Lightweight in-process pub/sub for decoupling side-effects.

Ported from OpenAlgo (`utils/event_bus.py`) — same shape, same guarantees:

- Topic-based routing
- Thread-safe subscribe / unsubscribe / publish
- Non-blocking publish: callbacks dispatch on a shared ThreadPoolExecutor
- Error isolation: a crashing subscriber doesn't break siblings or the publisher

Used by the strategy module to decouple risk-event audit writes, WebSocket
fan-out, and (later) Telegram alerts from the engine's hot path. See
``docs/plan/strategy-module.md`` for how the strategy engine consumes events.
"""

from __future__ import annotations

import logging
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class Event:
    """Base event class. All events inherit from this and set a ``topic``."""

    topic: str = ""


class EventBus:
    """In-process event bus with topic-based routing and async dispatch.

    All subscriber callbacks run in a shared thread pool, never blocking the
    publisher. Thread-safe for concurrent subscribe / unsubscribe / publish.
    """

    def __init__(self, workers: int = 10):
        self._subscribers: dict[str, list] = defaultdict(list)
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(
            max_workers=workers, thread_name_prefix="eventbus"
        )

    def subscribe(self, topic: str, callback, name: str = "") -> None:
        """Register a callback for a topic. Callback receives the Event object."""
        with self._lock:
            self._subscribers[topic].append(callback)
        cb_name = name or getattr(callback, "__name__", str(callback))
        logger.debug("EventBus: subscribed '%s' to '%s'", cb_name, topic)

    def unsubscribe(self, topic: str, callback) -> None:
        with self._lock:
            try:
                self._subscribers[topic].remove(callback)
            except ValueError:
                pass

    def publish(self, event: Event) -> None:
        """Publish an event to all subscribers of its topic. Non-blocking."""
        with self._lock:
            callbacks = list(self._subscribers.get(event.topic, []))
        for cb in callbacks:
            self._executor.submit(self._safe_call, cb, event)

    def _safe_call(self, cb, event: Event) -> None:
        try:
            cb(event)
        except Exception:
            cb_name = getattr(cb, "__name__", str(cb))
            logger.exception(
                "EventBus subscriber '%s' failed on '%s'", cb_name, event.topic
            )


# Process-wide singleton.
bus = EventBus()
