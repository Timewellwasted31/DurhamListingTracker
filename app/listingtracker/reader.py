"""Our own background page reader.

It asks for pages exactly the way Chrome does (the same connection fingerprint and request
headers), but runs no page scripts and draws nothing: it only brings back each page's HTML for
the site adapters to read. Listing and search pages carry their data in the HTML, so no browser
is needed, and sites that turn away automated browsers (Zolo, REW) answer it normally.

A site's saved sign-in (see siteaccounts) goes with its requests, and the cookies the site renews
along the way are kept for next time.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from datetime import date
from http.cookiejar import Cookie
from typing import Iterable, Optional
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

logger = logging.getLogger(__name__)

# The Chrome the reader presents itself as. Fixed here (and the library's version is pinned in
# requirements.txt) so that it only ever changes when we change it.
CHROME = "chrome152"
LANGUAGE = "en-CA,en-US;q=0.9,en;q=0.8"
TIMEOUT = 30
BLOCKED_STATUSES = (401, 403, 429)
# A connection the site closed while it sat idle (curl's HTTP/2 framing error, an empty reply, or
# a send or receive failure): the request is tried once more on a fresh connection
DROPPED = ("curl: (16)", "curl: (52)", "curl: (55)", "curl: (56)")


@dataclass
class Page:
    url: str  # where the site ended up, after any redirects
    status: int
    html: str

    @property
    def refused(self) -> bool:
        """The site said no (signed out, forbidden, or slow down)."""
        return self.status in BLOCKED_STATUSES


def _to_cookie(c: dict) -> Cookie:
    """A saved cookie (in the browser's storage format) as the reader keeps it."""
    domain = c.get("domain", "")
    expires = c.get("expires")
    return Cookie(
        version=0, name=c["name"], value=c.get("value", ""), port=None, port_specified=False,
        domain=domain, domain_specified=domain.startswith("."), domain_initial_dot=domain.startswith("."),
        path=c.get("path") or "/", path_specified=True, secure=bool(c.get("secure")),
        expires=int(expires) if expires and expires > 0 else None, discard=not (expires and expires > 0),
        comment=None, comment_url=None,
        rest={"HttpOnly": ""} if c.get("httpOnly") else {}, rfc2109=False,
    )


def _from_cookie(c: Cookie, http_only: frozenset = frozenset()) -> dict:
    return {
        "name": c.name, "value": c.value or "", "domain": c.domain, "path": c.path or "/",
        "expires": float(c.expires) if c.expires else -1,
        "httpOnly": c.has_nonstandard_attr("HttpOnly") or (c.name, c.domain.lstrip(".")) in http_only,
        "secure": bool(c.secure), "sameSite": "Lax",
    }


class Reader:
    """One person's visit to a site: pages are read one after another, each referring to the last.

    Use as a context manager so renewed sign-in cookies are saved when the visit ends.
    """

    def __init__(self, urls: Iterable[str] = ()):
        from curl_cffi import requests
        from listingtracker import siteaccounts

        self._urls = list(urls)
        self._session = requests.Session(impersonate=CHROME, timeout=TIMEOUT)
        self._session.headers.update({"Accept-Language": LANGUAGE})
        state = siteaccounts.storage_state_for(self._urls) or {}
        self.signed_in = bool(state)
        self._origins = state.get("origins") or []  # kept as they were: the reader has no page storage
        for c in state.get("cookies") or []:
            self._session.cookies.jar.set_cookie(_to_cookie(c))
        # Hidden from page scripts: a flag the library drops when a site renews the cookie
        self._http_only = frozenset((c["name"], c.get("domain", "").lstrip("."))
                                    for c in state.get("cookies") or [] if c.get("httpOnly"))
        self._last: Optional[str] = None

    def get(self, url: str) -> Page:
        """Read a page as if the person had followed a link to it from the previous page."""
        headers = {}
        if self._last and urlparse(self._last).netloc == urlparse(url).netloc:
            headers = {"Referer": self._last, "Sec-Fetch-Site": "same-origin"}
        r = self._send("GET", url, headers=headers, allow_redirects=True)
        self._last = str(r.url)
        return Page(url=str(r.url), status=r.status_code, html=r.text)

    def call(self, url: str, body: dict, *, page_origin: str, headers: Optional[dict] = None) -> tuple[int, Optional[dict]]:
        """A data request the open page makes itself (a JSON POST from its own scripts), sent with
        the headers Chrome gives such a request rather than a page load's. Returns (status, reply)."""
        import json
        page_origin = page_origin.rstrip("/")
        same_site = urlparse(url).netloc.split(".")[-2:] == urlparse(page_origin).netloc.split(".")[-2:]
        h = {
            "Accept": "application/json", "Content-Type": "application/json", "Origin": page_origin,
            # Chrome sends only the origin to another site
            "Referer": (self._last or page_origin + "/") if same_site else page_origin + "/",
            "Sec-Fetch-Site": "same-site" if same_site else "cross-site",
            "Sec-Fetch-Mode": "cors", "Sec-Fetch-Dest": "empty",
            # not sent with a script's request
            "Sec-Fetch-User": None, "Upgrade-Insecure-Requests": None, "Cache-Control": None,
            **(headers or {}),
        }
        # Compact, in the page's own key order, as its scripts serialise it
        r = self._send("POST", url, data=json.dumps(body, separators=(",", ":"), ensure_ascii=False),
                               headers=h, allow_redirects=False)
        try:
            return r.status_code, r.json()
        except ValueError:
            return r.status_code, None

    def _send(self, method: str, url: str, **kwargs):
        try:
            return self._session.request(method, url, **kwargs)
        except Exception as e:
            if not any(code in str(e) for code in DROPPED):
                raise
            logger.info("%s dropped the connection (%s); trying once more", urlparse(url).netloc, str(e)[:40])
            time.sleep(3)
            return self._session.request(method, url, **kwargs)

    def stored(self, origin: str, name: str) -> Optional[str]:
        """A value the site keeps in the browser's page storage, from the saved sign-in."""
        for o in self._origins:
            if o.get("origin", "").rstrip("/") == origin.rstrip("/"):
                for item in o.get("localStorage") or []:
                    if item.get("name") == name:
                        return item.get("value")
        return None

    def storage_state(self) -> dict:
        """The cookies as they are now, in the same form a browser would give them."""
        return {"cookies": [_from_cookie(c, self._http_only) for c in self._session.cookies.jar],
                "origins": self._origins}

    def close(self) -> None:
        from listingtracker import siteaccounts
        try:
            siteaccounts.keep_fresh(self, self._urls)
        finally:
            self._session.close()

    def __enter__(self) -> "Reader":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


# --- robots.txt ---------------------------------------------------------------------------------

_robots: dict[str, tuple[str, Optional[RobotFileParser]]] = {}
_robots_lock = threading.Lock()


def allowed(url: str, reader: Optional[Reader] = None) -> bool:
    """Whether the site's robots.txt lets us read this page. Each site's rules are read once a
    day, by the reader, rather than once per page. A site without readable rules allows it."""
    parts = urlparse(url)
    netloc = parts.netloc
    today = date.today().isoformat()
    with _robots_lock:
        cached = _robots.get(netloc)
    if not cached or cached[0] != today:
        rules = None
        try:
            own = reader is None
            r = reader or Reader()
            try:
                page = r._session.get(f"{parts.scheme or 'https'}://{netloc}/robots.txt", allow_redirects=True)
            finally:
                if own:
                    r._session.close()
            rules = RobotFileParser()
            if page.status_code in (401, 403):
                rules.disallow_all = True  # the standard reading: a site that refuses its rules refuses all
            elif page.status_code == 200 and "html" not in page.headers.get("content-type", ""):
                rules.parse(page.text.splitlines())
            else:
                rules = None
        except Exception as e:
            logger.debug("No robots.txt for %s: %s", netloc, e)
        cached = (today, rules)
        with _robots_lock:
            _robots[netloc] = cached
    rules = cached[1]
    return rules is None or rules.can_fetch("*", url)
