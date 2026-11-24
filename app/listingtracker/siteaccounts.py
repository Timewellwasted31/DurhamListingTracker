"""The user's own accounts on listing sites, used to see what those sites show only to members.

Many sites show sold prices and listing history only to signed-in members. The user signs in on
the site itself, in a browser window the app opens (their installed Opera GX, Chrome or Edge, with
a separate profile in the app's folder, never their everyday profile). The app never sees or
stores a password: while the window is open it reads that site's sign-in (cookies and page
storage) through the browser's built-in DevTools connection, and keeps it, encrypted for the
user's Windows account, for the page reader (reader.py) to use on that site.

Every sign-in is shared by all buyer profiles on this computer.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Iterable, Optional
from urllib.parse import urlparse

from listingtracker.paths import BROWSER_DIR, BROWSER_PROFILE_DIR

logger = logging.getLogger(__name__)

SESSIONS_DIR = BROWSER_DIR / "sessions"
STATE_PREFIX = "site_account:"      # + key -> ISO date the user signed in; empty when signed out
LAUNCH_ARGS = ["--no-first-run", "--no-default-browser-check"]
POLL_SECONDS = 2  # how often the open window's sign-in is copied


@dataclass(frozen=True)
class Site:
    key: str
    name: str
    url: str
    domains: tuple[str, ...]   # cookies kept: the site and the services its pages sign in through
    unlocks: str               # what members see, in the user's words


SITES = [
    Site("strata", "Strata", "https://strata.ca/", ("strata.ca", "slaterealty.ca"),
         "Sold prices and each building's sales history. Needed for Building Sales."),
    Site("zolo", "Zolo", "https://www.zolo.ca/", ("zolo.ca",),
         "Sold prices and listing history."),
    Site("condos", "Condos.ca", "https://condos.ca/", ("condos.ca",),
         "Sold and leased prices."),
    Site("housesigma", "HouseSigma", "https://housesigma.com/", ("housesigma.com",),
         "Sold prices and listing history."),
    Site("realtor", "REALTOR.ca", "https://www.realtor.ca/", ("realtor.ca",),
         "Listings are public; signing in adds little."),
    Site("rew", "REW", "https://www.rew.ca/", ("rew.ca",),
         "May show members more detail."),
    Site("condonow", "CondoNow", "https://www.condonow.com/", ("condonow.com",),
         "May show members more detail."),
    Site("condominiums", "Condominiums.ca", "https://www.condominiums.ca/", ("condominiums.ca",),
         "May show members more detail."),
]
SITES_BY_KEY = {s.key: s for s in SITES}

# Strata's profile predates the other sites; its folder name is kept so that sign-in still works.
_PROFILE_NAMES = {"strata": "strata-opera"}


class Busy(Exception):
    """The site's browser profile is already open (a sign-in window or a Building Sales run)."""


# --- Which site --------------------------------------------------------------------------------

def site_for_url(url: str) -> Optional[Site]:
    host = urlparse(url or "").netloc.lower()
    for s in SITES:
        d = s.domains[0]
        if host == d or host.endswith("." + d):
            return s
    return None


def _on_domain(cookie_domain: str, domains: Iterable[str]) -> bool:
    host = cookie_domain.lstrip(".").lower()
    return any(host == d or host.endswith("." + d) for d in domains)


# --- Signed in or not --------------------------------------------------------------------------

def signed_in_on(key: str) -> Optional[str]:
    """ISO date the user signed in to the site, or None."""
    from listingtracker.db import get_state
    try:
        value = get_state(STATE_PREFIX + key)
        if value is None and key == "strata":
            value = _carry_over_strata()
        return value or None
    except Exception as e:  # a busy or older database reads as signed out, not as an error
        logger.warning("Couldn't read the %s sign-in state: %s", key, e)
        return None


def _carry_over_strata() -> str:
    """Version 2.11.0 kept the Strata sign-in only in its browser profile, for Building Sales. A
    profile that wasn't found signed out then counts as signed in; the next Building Sales run
    saves its cookies for the rechecks too."""
    from listingtracker.db import get_state, set_state
    signed_in = profile_dir("strata").is_dir() and not get_state("building_sales_signed_out")
    value = date.today().isoformat() if signed_in else ""
    set_state(STATE_PREFIX + "strata", value)
    return value


def is_signed_in(key: str) -> bool:
    return bool(signed_in_on(key))


def mark_signed_in(key: str) -> None:
    from listingtracker.db import set_state
    set_state(STATE_PREFIX + key, date.today().isoformat())


def mark_signed_out(key: str) -> None:
    """The site no longer accepts the saved sign-in: stop using it until the user signs in again."""
    from listingtracker.db import set_state
    set_state(STATE_PREFIX + key, "")


def forget(key: str) -> None:
    """Sign out: delete the saved cookies and the app's browser profile for the site."""
    with claim(key):
        mark_signed_out(key)
        _session_path(key).unlink(missing_ok=True)
        shutil.rmtree(profile_dir(key), ignore_errors=True)


# --- The saved sign-in -------------------------------------------------------------------------

def _session_path(key: str) -> Path:
    return SESSIONS_DIR / f"{key}.bin"


def _crypt(data: bytes, protect: bool) -> bytes:
    """Windows DPAPI: only this Windows account on this computer can read what is protected."""
    if sys.platform != "win32":
        return data
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    buf = ctypes.create_string_buffer(data, len(data))
    src = Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    out = Blob()
    crypt32 = ctypes.windll.crypt32
    no_ui = 0x1
    if protect:
        ok = crypt32.CryptProtectData(ctypes.byref(src), ctypes.c_wchar_p("Durham Listing Tracker"),
                                      None, None, None, no_ui, ctypes.byref(out))
    else:
        ok = crypt32.CryptUnprotectData(ctypes.byref(src), None, None, None, None, no_ui, ctypes.byref(out))
    if not ok:
        raise ctypes.WinError()
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


def keep_only(state: dict, site: Site) -> dict:
    """The parts of a browser's storage that belong to the site (not, say, a Google sign-in)."""
    cookies = [c for c in state.get("cookies") or [] if _on_domain(c.get("domain", ""), site.domains)]
    origins = [o for o in state.get("origins") or []
               if _on_domain(urlparse(o.get("origin", "")).netloc, site.domains)]
    return {"cookies": cookies, "origins": origins}


def save_session(key: str, state: dict) -> None:
    state = keep_only(state, SITES_BY_KEY[key])
    if not state["cookies"] and not state["origins"]:
        return
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _session_path(key).with_suffix(".tmp")
    tmp.write_bytes(_crypt(json.dumps(state).encode("utf-8"), protect=True))
    os.replace(tmp, _session_path(key))


def load_session(key: str) -> Optional[dict]:
    path = _session_path(key)
    if not path.is_file():
        return None
    try:
        return json.loads(_crypt(path.read_bytes(), protect=False))
    except Exception as e:
        logger.warning("Saved sign-in for %s is unreadable: %s", key, e)
        return None


def merge(states: Iterable[dict]) -> dict:
    out = {"cookies": [], "origins": []}
    for s in states:
        out["cookies"].extend(s.get("cookies") or [])
        out["origins"].extend(s.get("origins") or [])
    return out


def storage_state_for(urls: Iterable[str]) -> Optional[dict]:
    """Saved sign-ins for the signed-in sites among these pages, ready for browser.new_context()."""
    keys = {s.key for u in urls if (s := site_for_url(u))}
    states = [st for k in sorted(keys) if is_signed_in(k) and (st := load_session(k))]
    return merge(states) if states else None


def keep_fresh(context, urls: Iterable[str]) -> None:
    """Sites renew their sign-in cookies as they're used; keep the renewed ones for next time."""
    keys = {s.key for u in urls if (s := site_for_url(u))}
    keys = [k for k in keys if is_signed_in(k)]
    if not keys:
        return
    try:
        state = context.storage_state()
    except Exception as e:
        logger.debug("Couldn't read the browser's cookies: %s", e)
        return
    for k in keys:
        save_session(k, state)


# --- The user's browser ------------------------------------------------------------------------

def profile_dir(key: str) -> Path:
    return BROWSER_PROFILE_DIR / _PROFILE_NAMES.get(key, f"{key}-account")


_claimed: set[str] = set()
_claim_lock = threading.Lock()


@contextmanager
def claim(key: str):
    """Only one window may use a site's browser profile at a time."""
    with _claim_lock:
        if key in _claimed:
            raise Busy(f"{SITES_BY_KEY[key].name} is already open in another window")
        _claimed.add(key)
    try:
        yield
    finally:
        with _claim_lock:
            _claimed.discard(key)


def _newest(root: Path) -> Optional[Path]:
    import re
    versions = [d for d in root.glob("*") if re.fullmatch(r"[\d.]+", d.name) and (d / "opera.exe").is_file()]
    if not versions:
        return None
    return max(versions, key=lambda d: tuple(int(n) for n in d.name.split("."))) / "opera.exe"


def installed_browser() -> tuple[str, Optional[str]]:
    """The user's own browser, as (name, its program), or ("", None) when none is found.

    An everyday browser: Google refuses its sign-in in a test build ("Error 400"), and sites are
    less likely to flag the account. For Opera, the newest version folder's opera.exe is used: its
    root launcher hands off and exits."""
    local = Path(os.environ.get("LOCALAPPDATA", ""))
    for name, folder in (("Opera GX", "Opera GX"), ("Opera", "Opera")):
        exe = _newest(local / "Programs" / folder)
        if exe:
            return name, str(exe)
    roots = [Path(os.environ.get(v, "")) for v in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")]
    for name, rel in (("Chrome", ("Google", "Chrome", "Application", "chrome.exe")),
                      ("Edge", ("Microsoft", "Edge", "Application", "msedge.exe"))):
        for root in roots:
            exe = root.joinpath(*rel)
            if exe.is_file():
                return name, str(exe)
    return "", None


class BrowserWindow:
    """The user's browser, opened on a site's own profile folder, with a small connection to it
    (the browser's built-in DevTools protocol, local to this computer and open only while the
    window is) used for one thing: reading that site's cookies and page storage."""

    def __init__(self, exe: str, folder: Path, url: str, extra_args: Iterable[str] = ()):
        import subprocess
        folder.mkdir(parents=True, exist_ok=True)
        port_file = folder / "DevToolsActivePort"
        port_file.unlink(missing_ok=True)
        self.proc = subprocess.Popen([exe, f"--user-data-dir={folder}", "--remote-debugging-port=0",
                                      *LAUNCH_ARGS, *extra_args, url])
        self.port = None
        for _ in range(60):  # the browser writes its port once it is up
            try:
                self.port = int(port_file.read_text(encoding="utf-8").split()[0])
                break
            except (OSError, ValueError, IndexError):
                time.sleep(0.5)
        if not self.port:
            self.close()
            raise RuntimeError("The browser didn't open its window")

    def _json(self, path: str):
        from curl_cffi import requests
        return requests.get(f"http://127.0.0.1:{self.port}{path}", timeout=5).json()

    def is_open(self) -> bool:
        try:
            return any(t.get("type") == "page" for t in self._json("/json/list"))
        except Exception:
            return False

    @staticmethod
    def _ask(ws_url: str, method: str, params: Optional[dict] = None) -> dict:
        from curl_cffi.requests import WebSocket
        ws = WebSocket()
        ws.connect(ws_url, timeout=10)
        try:
            ws.send_str(json.dumps({"id": 1, "method": method, "params": params or {}}))
            while True:
                reply = json.loads(ws.recv_str())
                if reply.get("id") == 1:
                    if "error" in reply:
                        raise RuntimeError(reply["error"].get("message", "DevTools error"))
                    return reply.get("result") or {}
        finally:
            ws.close()

    def storage_state(self, site: "Site") -> dict:
        """The site's cookies and page storage, in the form save_session() keeps."""
        browser_ws = self._json("/json/version")["webSocketDebuggerUrl"]
        cookies = []
        for c in self._ask(browser_ws, "Storage.getCookies").get("cookies") or []:
            if _on_domain(c.get("domain", ""), site.domains):
                cookies.append({k: c[k] for k in ("name", "value", "domain", "path", "expires",
                                                  "httpOnly", "secure", "sameSite") if k in c})
        origins: dict[str, list] = {}
        for t in self._json("/json/list"):
            page = urlparse(t.get("url", ""))
            if t.get("type") != "page" or not _on_domain(page.netloc, site.domains):
                continue
            origin = f"{page.scheme}://{page.netloc}"
            result = self._ask(t["webSocketDebuggerUrl"], "Runtime.evaluate", {
                "expression": "JSON.stringify(Object.entries(localStorage))", "returnByValue": True})
            items = json.loads((result.get("result") or {}).get("value") or "[]")
            origins[origin] = [{"name": k, "value": v} for k, v in items]
        return {"cookies": cookies, "origins": [{"origin": o, "localStorage": v} for o, v in origins.items()]}

    def close(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()


def open_sign_in(key: str) -> None:
    """Open the site in the user's browser for them to sign in; returns when they close it.

    While the window is open, the site's sign-in is copied every couple of seconds, so the last
    copy is the signed-in one however the window gets closed."""
    site = SITES_BY_KEY[key]
    name, exe = installed_browser()
    if not exe:
        raise RuntimeError("No Opera, Chrome or Edge was found on this computer to sign in with")
    with claim(key):
        window = BrowserWindow(exe, profile_dir(key), site.url)
        latest = None
        try:
            while window.is_open():
                try:
                    latest = window.storage_state(site)
                except Exception as e:  # mid-navigation, or closing
                    logger.debug("Couldn't read %s's sign-in yet: %s", site.name, e)
                time.sleep(POLL_SECONDS)
        finally:
            window.close()
    if latest:
        save_session(key, latest)


def sign_in_status() -> list[dict]:
    """Every site with whether the user is signed in, for the profile window."""
    return [{"site": s, "signed_in": signed_in_on(s.key)} for s in SITES]
