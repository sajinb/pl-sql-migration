"""Captures print/log output from pipeline nodes and streams to WebSocket clients."""

from __future__ import annotations

import asyncio
import io
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Optional


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


# Per-run broadcasters (keyed by run_id, not project_id)
_broadcasters: dict[int, LogBroadcaster] = {}


def get_broadcaster(run_id: int) -> LogBroadcaster:
    if run_id not in _broadcasters:
        _broadcasters[run_id] = LogBroadcaster()
    return _broadcasters[run_id]


def remove_broadcaster(run_id: int) -> None:
    _broadcasters.pop(run_id, None)


class _CapturingWriter:
    """Wraps sys.stdout to intercept print() calls from pipeline nodes.

    Emits to WebSocket AND persists to migration_logs via an async helper
    running on a separate event loop in the pipeline thread.
    """

    def __init__(
        self,
        original: io.TextIOBase,
        broadcaster: LogBroadcaster,
        stage_holder: list,
        run_id: int,
    ):
        self._original = original
        self._broadcaster = broadcaster
        self._stage_holder = stage_holder
        self._run_id = run_id

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
            # Persist to DB (fire-and-forget from sync context)
            _persist_log_sync(self._run_id, stage, level, stripped)
        return len(text)

    def flush(self) -> None:
        self._original.flush()

    def __getattr__(self, name):
        return getattr(self._original, name)


def _persist_log_sync(run_id: int, stage: str, level: str, message: str) -> None:
    """Persist a log entry from a synchronous (thread) context."""
    import asyncio
    from tsql_migration.api.database import async_session
    from tsql_migration.api.models import LogLevel, MigrationLog

    async def _insert():
        async with async_session() as db:
            db.add(MigrationLog(
                run_id=run_id,
                stage=stage,
                level=LogLevel(level),
                message=message,
            ))
            await db.commit()

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_insert())
    except Exception:
        pass  # Don't let log persistence failures break the pipeline
    finally:
        loop.close()


@contextmanager
def capture_stdout(broadcaster: LogBroadcaster, stage_holder: list, run_id: int):
    """Context manager that redirects stdout through the broadcaster."""
    original = sys.stdout
    sys.stdout = _CapturingWriter(original, broadcaster, stage_holder, run_id)
    try:
        yield
    finally:
        sys.stdout = original
