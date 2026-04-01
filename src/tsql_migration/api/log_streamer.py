"""Captures print/log output from pipeline nodes and streams to WebSocket clients."""

from __future__ import annotations

import asyncio
import io
import sys
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Optional

from fastapi import WebSocket


@dataclass
class LogEntry:
    stage: str
    message: str
    level: str = "info"  # info, warning, error


class LogBroadcaster:
    """Thread-safe broadcaster that fans out log entries to WebSocket clients."""

    def __init__(self) -> None:
        self._subscribers: dict[int, asyncio.Queue[Optional[LogEntry]]] = {}
        self._next_id = 0
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self) -> tuple[int, asyncio.Queue[Optional[LogEntry]]]:
        queue: asyncio.Queue[Optional[LogEntry]] = asyncio.Queue()
        sub_id = self._next_id
        self._next_id += 1
        self._subscribers[sub_id] = queue
        return sub_id, queue

    def unsubscribe(self, sub_id: int) -> None:
        self._subscribers.pop(sub_id, None)

    def emit(self, entry: LogEntry) -> None:
        """Called from the pipeline thread — schedule puts on the async loop."""
        for queue in self._subscribers.values():
            if self._loop and self._loop.is_running():
                self._loop.call_soon_threadsafe(queue.put_nowait, entry)

    def close_all(self) -> None:
        """Signal end-of-stream to all subscribers."""
        for queue in self._subscribers.values():
            if self._loop and self._loop.is_running():
                self._loop.call_soon_threadsafe(queue.put_nowait, None)


# Per-project broadcasters
_broadcasters: dict[int, LogBroadcaster] = {}


def get_broadcaster(project_id: int) -> LogBroadcaster:
    if project_id not in _broadcasters:
        _broadcasters[project_id] = LogBroadcaster()
    return _broadcasters[project_id]


def remove_broadcaster(project_id: int) -> None:
    _broadcasters.pop(project_id, None)


class _CapturingWriter:
    """Wraps sys.stdout to intercept print() calls from pipeline nodes."""

    def __init__(self, original: io.TextIOBase, broadcaster: LogBroadcaster, stage_holder: list):
        self._original = original
        self._broadcaster = broadcaster
        self._stage_holder = stage_holder  # mutable list holding current stage name

    def write(self, text: str) -> int:
        self._original.write(text)
        stripped = text.strip()
        if stripped:
            stage = self._stage_holder[0] if self._stage_holder else "unknown"
            level = "info"
            if "ERROR" in stripped or "FAILED" in stripped:
                level = "error"
            elif "WARNING" in stripped or "SKIP" in stripped:
                level = "warning"
            self._broadcaster.emit(LogEntry(stage=stage, message=stripped, level=level))
        return len(text)

    def flush(self) -> None:
        self._original.flush()

    # Forward other attributes to the original
    def __getattr__(self, name):
        return getattr(self._original, name)


@contextmanager
def capture_stdout(broadcaster: LogBroadcaster, stage_holder: list):
    """Context manager that redirects stdout through the broadcaster."""
    original = sys.stdout
    sys.stdout = _CapturingWriter(original, broadcaster, stage_holder)
    try:
        yield
    finally:
        sys.stdout = original
