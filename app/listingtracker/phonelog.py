"""Problems the phone ran into, reported to the computer once it gets through again.

The phone can't tell the computer about a request that failed while the connection was down,
so it keeps each failure and sends them with the next request that works. Every phone request
carries an ID, and the computer remembers the recent ones it received; each report then says
whether the failed request ever reached the computer (a network problem) or did, and what the
computer answered (a problem here).

Reports go to logs/phone-errors.log, one line each, newest last.

Every connection the phone makes is noted too (see `connection`): where it reached the computer
from, which of the computer's addresses it used, and the phone's own network and address as it
reports them. logs/phone-connections.log gets a line each time that combination changes, and
logs/phone-connections.json keeps a tally of every combination seen, with the number of requests.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Optional

from listingtracker.paths import LOGS_DIR

logger = logging.getLogger(__name__)

LOG_PATH = LOGS_DIR / "phone-errors.log"
MAX_REPORTS = 200            # per upload
MAX_TEXT = 300               # characters kept of any text field
REMEMBERED_REQUESTS = 5000   # a few hours of a phone's requests

_seen: "OrderedDict[str, tuple[float, str, int, int]]" = OrderedDict()  # id -> (time, path, status, ms)
_seen_lock = threading.Lock()
_started = time.time()

FIELDS = {  # what a report may carry, and its type
    "id": str, "at": str, "kind": str, "method": str, "path": str, "status": int, "message": str,
    "attempt": int, "of": int, "ms": int, "request_id": str, "online": bool, "network": str,
    "rtt": int, "downlink": float, "visible": str, "server": str, "source": str,
}


CONNECTIONS_LOG = LOGS_DIR / "phone-connections.log"
CONNECTIONS_PATH = LOGS_DIR / "phone-connections.json"
SAVE_EVERY = 60  # seconds between tally saves while nothing changes
_LOCAL = ("127.0.0.1", "::1", "localhost")

_connections: Optional[dict] = None   # "source|route|phone" -> tally, loaded on first use
_current: Optional[str] = None        # the combination of the latest request
_phone_at: dict[str, str] = {}        # source -> what the phone last said about its network
_saved_at = 0.0
_connections_lock = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def connection(source: Optional[str], route: Optional[str], phone: Optional[str]) -> None:
    """Note one phone request: `source` is the address it came from, `route` the address of this
    computer it was sent to (host:port), and `phone` the phone's own network and address as it
    reports them. A request without that report (the page itself, an older app) is taken to come
    from the phone last heard from at the same source. This computer's own requests are skipped."""
    global _connections, _current, _saved_at
    if not source or source in _LOCAL:
        return
    route = (route or "?")[:MAX_TEXT]
    with _connections_lock:
        if phone:
            _phone_at[source] = phone = phone[:MAX_TEXT]
        else:
            phone = _phone_at.get(source, "not reported")
        if _connections is None:
            try:
                _connections = json.loads(CONNECTIONS_PATH.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                _connections = {}
        key = f"{source}|{route}|{phone}"
        now = _now()
        entry = _connections.get(key)
        new = entry is None
        if new:
            entry = _connections[key] = {"source": source, "route": route, "phone": phone,
                                         "first": now, "last": now, "requests": 0}
        entry["last"] = now
        entry["requests"] += 1
        changed, _current = key != _current, key
        if changed:
            note = ("first time" if new else
                    f"seen before: {entry['requests'] - 1} requests since {entry['first']}")
            _append(CONNECTIONS_LOG, f"{now} | phone {phone} | reached this computer from {source} "
                                     f"via {route} | {note} | {len(_connections)} different connections so far")
        if changed or time.time() - _saved_at > SAVE_EVERY:
            _saved_at = time.time()
            try:
                LOGS_DIR.mkdir(parents=True, exist_ok=True)
                CONNECTIONS_PATH.write_text(json.dumps(_connections, indent=1), encoding="utf-8")
            except OSError as e:
                logger.warning("Couldn't write %s: %s", CONNECTIONS_PATH, e)


def _append(path, line: str) -> None:
    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError as e:
        logger.warning("Couldn't write %s: %s", path, e)


def remember(request_id: Optional[str], path: str, status: int, ms: int) -> None:
    """Note a phone request the computer received."""
    if not request_id:
        return
    with _seen_lock:
        _seen[request_id[:64]] = (time.time(), path, status, ms)
        while len(_seen) > REMEMBERED_REQUESTS:
            _seen.popitem(last=False)


def _clean(report: dict) -> Optional[dict]:
    if not isinstance(report, dict):
        return None
    out = {}
    for key, kind in FIELDS.items():
        value = report.get(key)
        if value is None:
            continue
        try:
            if kind is str:
                out[key] = str(value)[:MAX_TEXT].replace("\n", " ").replace("\r", " ")
            elif kind is bool:
                out[key] = bool(value)
            else:
                out[key] = kind(value)
        except (TypeError, ValueError):
            continue
    return out or None


def _when(report: dict) -> Optional[float]:
    try:
        return datetime.fromisoformat(report["at"].replace("Z", "+00:00")).timestamp()
    except (KeyError, ValueError):
        return None


def reached_computer(report: dict) -> str:
    """Whether the failed request got here, from the IDs the computer remembers."""
    rid = report.get("request_id")
    if not rid:
        return ""
    with _seen_lock:
        seen = _seen.get(rid)
    if seen:
        _, _, status, ms = seen
        return f"reached the computer, which answered {status} in {ms} ms"
    when = _when(report)
    if when is not None and when < _started:
        return "unknown: the computer's app was restarted since"
    with _seen_lock:
        oldest = next(iter(_seen.values()))[0] if len(_seen) >= REMEMBERED_REQUESTS else None
    if oldest and when is not None and when < oldest:
        return "unknown: too long ago"
    return "never reached the computer"


def describe(report: dict) -> str:
    """One readable line for the log."""
    parts = [report.get("at", "?"), (report.get("kind") or "?").upper()]
    if report.get("path"):
        parts.append(f"{report.get('method', 'GET')} {report['path']}")
    if report.get("status"):
        parts.append(f"HTTP {report['status']}")
    if report.get("of"):
        parts.append(f"try {report.get('attempt', '?')}/{report['of']}")
    if report.get("ms") is not None:
        parts.append(f"after {report['ms']} ms")
    if report.get("message"):
        parts.append(repr(report["message"]))
    network = report.get("network") or "?"
    if report.get("rtt") is not None:
        network += f", rtt {report['rtt']} ms"
    parts.append(f"phone {'online' if report.get('online', True) else 'OFFLINE'} ({network})")
    if report.get("visible") and report["visible"] != "visible":
        parts.append(f"page {report['visible']}")
    if report.get("server"):
        parts.append(f"via {report['server']}")
    if report.get("source"):
        parts.append(f"at {report['source']}")
    if report.get("reached"):
        parts.append(report["reached"])
    return " | ".join(parts)


def record(reports: list, agent: str = "") -> int:
    """Write the phone's reports to the log. Returns how many were kept."""
    kept = [r for r in (_clean(x) for x in (reports or [])[:MAX_REPORTS]) if r]
    if not kept:
        return 0
    for r in kept:
        r["reached"] = reached_computer(r)
    received = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    lines = [f"--- {received}: {len(kept)} from the phone ({str(agent)[:MAX_TEXT]})"]
    lines += [describe(r) for r in kept]
    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except OSError as e:
        logger.warning("Couldn't write %s: %s", LOG_PATH, e)
    never = sum(1 for r in kept if r["reached"] == "never reached the computer")
    logger.warning("The phone reported %d problem(s), %d of them requests that never reached the "
                   "computer; details in %s", len(kept), never, LOG_PATH.name)
    return len(kept)

