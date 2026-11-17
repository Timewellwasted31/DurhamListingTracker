"""Refreshing photos, price history and earlier sales from listing pages saved on disk."""

import gzip

import pytest

from listingtracker import db, savedpages
from listingtracker.models import Snapshot, Status


class StubAdapter:
    site_name = "stub"

    def parse(self, html, url=""):
        out = {}
        if "history" in html:
            out.update(list_date="2026-06-01", price_history=[("2026-06-01", 500000.0)])
        if "sales" in html:
            out["past_sales"] = [("2015-05-11", 338800.0)]
        return out

    def extract_images(self, html, url=""):
        return ["https://cdn.example/a.jpg", "https://cdn.example/b.jpg"] if "photos" in html else []


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(db, "SEED_URLS", [])
    pages = tmp_path / "html"
    monkeypatch.setattr("listingtracker.paths.HTML_CACHE_DIR", pages)
    monkeypatch.setattr("listingtracker.fetcher.get_adapter_for_url", lambda url: StubAdapter())
    db.init_db()

    def save_page(listing_id, html, day):
        sid = db.add_snapshot(Snapshot(listing_id=listing_id, fetched_at=day + "T12:00:00",
                                       status=Status.FOR_SALE, list_price=500000.0))
        (pages / day).mkdir(parents=True, exist_ok=True)
        with gzip.open(pages / day / f"{sid}.html.gz", "wt", encoding="utf-8") as f:
            f.write(html)

    def listing_with_page(url, html, day="2026-11-10"):
        lid = db.add_listing(url, "stub").id
        save_page(lid, html, day)
        return lid

    listing_with_page.save_page = save_page
    return listing_with_page


def test_refresh_fills_each_kind_of_data_a_page_has(env):
    full = env("https://example.com/1", "photos history sales")
    bare = env("https://example.com/2", "nothing")
    assert savedpages.refresh() == {"price history": 1, "earlier sales": 1, "photos": 1, "area sales": 0}
    assert len(db.get_listing_images(full)) == 2
    assert db.get_price_histories(full)[full][0] == "2026-06-01"
    assert db.get_past_sales() == {full: [("2015-05-11", 338800.0)]}
    assert not db.get_listing_images(bare)


def test_refresh_reads_the_newest_page(env):
    lid = env("https://example.com/1", "nothing yet", day="2026-11-01")
    env.save_page(lid, "photos", "2026-11-12")
    assert savedpages.refresh()["photos"] == 1


def test_refresh_runs_once_per_version(env, monkeypatch):
    env("https://example.com/1", "photos")
    assert savedpages.refresh()["photos"] == 1
    assert savedpages.refresh() == {"price history": 0, "earlier sales": 0, "photos": 0, "area sales": 0}
    monkeypatch.setattr(savedpages, "PAGE_DATA_VERSION", savedpages.PAGE_DATA_VERSION + 1)
    env("https://example.com/2", "photos")
    assert savedpages.refresh()["photos"] == 1  # a new version reads every page again


def test_existing_photos_are_kept(env):
    lid = env("https://example.com/1", "photos")
    db.save_listing_images(lid, ["https://cdn.example/original.jpg"])
    savedpages.refresh()
    assert [i["url"] for i in db.get_listing_images(lid)] == ["https://cdn.example/original.jpg"]
