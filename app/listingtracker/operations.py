"""The desktop's long jobs (Recheck All, Auto-Search), as the phone sees them.

The desktop window registers a handler that starts a job on its own thread; the phone server
asks through `request()` and follows along with `status()`. Jobs started at the desktop are
reported the same way, so the phone always shows what the computer is doing.
"""

from __future__ import annotations

import threading
from datetime import datetime
from typing import Callable, Optional

JOBS = {"recheck": "Checking all listings", "search": "Searching for new listings",
        "sales": "Reading building sales"}
PROFILE_PREFIX = "profile:"  # a handler message that switches the buyer profile instead


class Operations:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._handler: Optional[Callable[[str], None]] = None
        self._running: Optional[str] = None
        self._text = ""
        self._current = 0
        self._total = 0
        self._started: Optional[str] = None
        self._last: Optional[dict] = None

    # --- The desktop window ---

    def attach(self, handler: Optional[Callable[[str], None]]) -> None:
        """handler(message) starts "recheck", "search", "sales" or "cancel", or switches to the profile in
        "profile:<name>"; it must be safe to call from any thread (the window passes a Qt
        signal's emit, which queues to its own thread)."""
        with self._lock:
            self._handler = handler
            if handler is None:
                self._running = None

    def started(self, job: str, total: int = 0, text: str = "") -> None:
        with self._lock:
            self._running, self._total, self._current = job, total, 0
            self._text = text or JOBS.get(job, job)
            self._started = datetime.now().isoformat(timespec="seconds")

    def progress(self, current: int, total: int, text: str) -> None:
        with self._lock:
            if self._running:
                self._current, self._total, self._text = current, total, text

    def finished(self, message: str, ok: bool = True) -> None:
        """The job ended (or never started); `message` is what the phone shows."""
        with self._lock:
            self._last = {"job": self._running, "message": message, "ok": ok,
                          "at": datetime.now().isoformat(timespec="seconds")}
            self._running = None
            self._current = self._total = 0
            self._text = ""

    def refused(self, job: str, message: str) -> None:
        """A request the window turned down (busy, no profile, ...), reported like a result."""
        with self._lock:
            self._last = {"job": job, "message": message, "ok": False,
                          "at": datetime.now().isoformat(timespec="seconds")}

    # --- The phone server ---

    @property
    def available(self) -> bool:
        return self._handler is not None

    def request(self, job: str) -> None:
        if job not in (*JOBS, "cancel"):
            raise ValueError(job)
        self._send(job)

    def request_profile(self, name: str) -> None:
        """Switch the window to buyer profile `name` ("" for none), as its profile menu does."""
        self._send(PROFILE_PREFIX + name)

    def _send(self, message: str) -> None:
        handler = self._handler
        if handler is None:
            raise RuntimeError("The desktop app isn't open")
        handler(message)

    def status(self) -> dict:
        with self._lock:
            return {
                "available": self._handler is not None,
                "running": self._running,
                "text": self._text,
                "current": self._current,
                "total": self._total,
                "started": self._started if self._running else None,
                "last": self._last,
            }


operations = Operations()
