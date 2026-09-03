"""Bounded telemetry only: no game function calls, input or save access."""

from __future__ import annotations

import threading
import time
from collections import Counter, deque


class Observation:
    def __init__(self, capacity: int = 1024, focus_reader=None):
        self.lock = threading.Lock()
        self.events = deque(maxlen=capacity)
        self.counts = Counter()
        self.active = False
        self.paused = False
        self.stopped = False
        self.dropped = 0
        self.focus_reader = focus_reader
        self._append("ready")

    def _append(self, event, **details):
        # Capture at the producer, not when the worker eventually writes the event.
        # Keep this off the sampled update paths, which only increment counters.
        if self.focus_reader is not None:
            try:
                details["game_foreground"] = self.focus_reader()
            except Exception:
                details["game_foreground"] = None  # Unavailable is not background evidence.
        if len(self.events) == self.events.maxlen:
            self.dropped += 1
        self.events.append(
            {
                "monotonic": time.monotonic(),
                "event": event,
                "thread_id": threading.get_native_id(),
                **details,
            }
        )

    def command(self, action: str):
        with self.lock:
            if self.stopped:
                return
            if action == "stop":
                self.stopped, self.active = True, False
                self._append("stopped", counts=dict(self.counts), dropped=self.dropped)
            elif action == "start" and not self.active:
                self.active, self.paused = True, False
                self._append("started")
            elif action == "pause" and self.active:
                self.paused = not self.paused
                self._append("paused" if self.paused else "resumed")

    def record(self, event: str, *, sampled: bool = False, **details):
        with self.lock:
            if not self.active or self.paused or self.stopped:
                return
            self.counts[event] += 1
            # Hot paths only increment counters. The writer samples these once a second.
            if not sampled:
                self._append(event, **details)

    def drain(self):
        with self.lock:
            result = list(self.events)
            self.events.clear()
            return result

    def notice(self, event: str, **details):
        # Automation control/completion events remain visible while actions are paused.
        with self.lock:
            self._append(event, **details)

    def snapshot(self):
        with self.lock:
            return {
                "active": self.active,
                "paused": self.paused,
                "stopped": self.stopped,
                "counts": dict(self.counts),
                "dropped": self.dropped,
            }
