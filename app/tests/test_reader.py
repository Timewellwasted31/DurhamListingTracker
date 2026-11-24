"""Our own page reader: what it sends, what it keeps, and when it stays away."""

import http.server
import threading

import pytest

from listingtracker import reader, siteaccounts


class Site(http.server.BaseHTTPRequestHandler):
    """A small listing site on this computer that remembers what it was asked."""
    requests: list = []

    def do_GET(self):
        Site.requests.append((self.path, self.headers))  # looked up without regard to case
        if self.path == "/robots.txt":
            body, status, kind = b"User-agent: *\nDisallow: /private/\n", 200, "text/plain"
        elif self.path == "/forbidden":
            body, status, kind = b"<html>Access denied</html>", 403, "text/html"
        else:
            body, status, kind = f"<html>{self.path}</html>".encode(), 200, "text/html"
        self.send_response(status)
        self.send_header("Content-Type", kind)
        if self.path == "/renew":
            self.send_header("Set-Cookie", "session=renewed; Path=/; Max-Age=3600; HttpOnly")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture
def site(monkeypatch):
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Site)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    Site.requests = []
    monkeypatch.setattr(reader, "_robots", {})
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def test_saved_sign_in_goes_with_each_page_and_renewed_cookies_are_kept(site, monkeypatch):
    saved = {"cookies": [{"name": "session", "value": "old", "domain": "127.0.0.1", "path": "/",
                          "expires": 4102444800, "httpOnly": True, "secure": False}],
             "origins": [{"origin": site, "localStorage": [{"name": "auth", "value": "a"}]}]}
    monkeypatch.setattr(siteaccounts, "storage_state_for", lambda urls: saved)
    kept = []
    monkeypatch.setattr(siteaccounts, "keep_fresh", lambda r, urls: kept.append(r.storage_state()))

    with reader.Reader([site + "/a"]) as r:
        assert r.signed_in
        first = r.get(site + "/search")
        r.get(site + "/renew")
        r.get(site + "/listing")
    assert first.status == 200 and first.html == "<html>/search</html>" and not first.refused

    (_, h1), (_, h2), (_, h3) = Site.requests
    assert h1["Cookie"] == "session=old" and "Referer" not in h1
    assert h1["User-Agent"].startswith("Mozilla/5.0 (Windows NT 10.0") and "Headless" not in h1["User-Agent"]
    assert h2["Referer"] == site + "/search" and h2["Sec-Fetch-Site"] == "same-origin"
    assert h3["Cookie"] == "session=renewed"

    cookie = kept[0]["cookies"][0]
    assert (cookie["name"], cookie["value"], cookie["httpOnly"]) == ("session", "renewed", True)
    assert cookie["expires"] > 0
    assert kept[0]["origins"] == saved["origins"]  # the sign-in's page storage is passed on untouched


def test_a_refusal_is_reported(site, monkeypatch):
    monkeypatch.setattr(siteaccounts, "storage_state_for", lambda urls: None)
    with reader.Reader() as r:
        page = r.get(site + "/forbidden")
    assert page.refused and page.status == 403 and not r.signed_in


def test_robots_rules_are_read_once_and_obeyed(site):
    assert reader.allowed(site + "/oshawa/condos")
    assert not reader.allowed(site + "/private/x")
    assert reader.allowed(site + "/another")
    assert [p for p, _ in Site.requests] == ["/robots.txt"]


def test_saved_cookies_keep_their_domain_and_expiry():
    c = {"name": "s", "value": "v", "domain": ".strata.ca", "path": "/", "expires": 1900000000.0,
         "httpOnly": True, "secure": True}
    back = reader._from_cookie(reader._to_cookie(c))
    assert {k: back[k] for k in c} == c
    session_only = reader._from_cookie(reader._to_cookie({**c, "domain": "strata.ca", "expires": -1}))
    assert session_only["expires"] == -1 and session_only["domain"] == "strata.ca"



def test_a_dropped_connection_is_tried_once_more(monkeypatch):
    monkeypatch.setattr(siteaccounts, "storage_state_for", lambda urls: None)
    monkeypatch.setattr(reader.time, "sleep", lambda s: None)
    with reader.Reader() as r:
        calls = []

        class Reply:
            status_code, url, text = 200, "https://strata.ca/x", "<html>ok</html>"

        def flaky(method, url, **kw):
            calls.append(method)
            if len(calls) == 1:
                raise RuntimeError("Failed to perform, curl: (16) Error in the HTTP2 framing layer.")
            return Reply()
        monkeypatch.setattr(r._session, "request", flaky)
        assert r.get("https://strata.ca/x").html == "<html>ok</html>" and calls == ["GET", "GET"]

        def refused(method, url, **kw):
            raise RuntimeError("Failed to perform, curl: (28) Operation timed out")
        monkeypatch.setattr(r._session, "request", refused)
        import pytest
        with pytest.raises(RuntimeError, match="timed out"):  # not a dropped connection: not retried
            r.get("https://strata.ca/x")
