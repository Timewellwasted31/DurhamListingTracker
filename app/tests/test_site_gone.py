"""A listing site that closes or redirects must not wipe what's stored (wahi.com, Nov 13 2026)."""

import re

from listingtracker import fetcher
from listingtracker.adapters.base import UNIT_PATTERN
from listingtracker.adapters.wahi import WahiAdapter
from listingtracker.models import Listing


def unit(text):
    m = re.search(UNIT_PATTERN, text, re.IGNORECASE)
    return m.group(1) if m else None


def test_unit_numbers_are_read_but_css_colours_are_not():
    assert unit("Unit 410, 712 Rossland Rd E") == "410"
    assert unit("Suite #12B") == "12B"
    assert unit("<h1>#1414 - 1695 Dersan St</h1>") == "1414"
    assert unit("body{color:#58595b;background:#202124}") is None
    assert unit("border:1px solid #000;") is None


class RedirectedReader:
    """A site that sends every page to another site, as wahi.com now does."""

    def get(self, url):
        from listingtracker.reader import Page
        return Page(url="https://johnstonanddaniel.com/?redirect_source=wahi", status=200,
                    html="<html><style>a{color:#58595b}</style>Luxury Real Estate</html>")


def test_a_redirect_to_another_site_keeps_the_stored_listing(monkeypatch):
    saved = []
    monkeypatch.setattr(fetcher, "_check_robots", lambda *a: True)
    monkeypatch.setattr(fetcher.time, "sleep", lambda s: None)
    monkeypatch.setattr(fetcher, "add_snapshot", lambda snap: saved.append(snap))
    monkeypatch.setattr(fetcher, "update_listing", lambda lst: saved.append(lst))
    listing = Listing(id=1, url="https://wahi.com/ca/en/real-estate/on/gta/durham/x/1-a-st-l1t3l8-ontario",
                      site="wahi.com", address="1 A St", unit=None)
    assert fetcher.fetch_listing(listing, RedirectedReader(), WahiAdapter()) == (None, [])
    assert saved == [] and listing.unit is None
