"""Tests for site adapters — each fixture pair validates selectors."""

import pytest
from pathlib import Path

FIXTURES_DIR = Path(__file__).parent / "fixtures"


class TestStrataAdapter:
    def test_matches_url(self):
        from listingtracker.adapters.strata import StrataAdapter

        adapter = StrataAdapter()
        assert adapter.matches("https://strata.ca/pickering/1210-radom-st") is True
        assert adapter.matches("https://www.strata.ca/whitby/711-rossland") is True
        assert adapter.matches("https://realtor.ca/something") is False

    def test_parse_bayshore_fixture(self):
        from listingtracker.adapters.strata import StrataAdapter
        from tests.fixtures.strata_bayshore_expected import EXPECTED

        html_path = FIXTURES_DIR / "strata_bayshore.html"
        html = html_path.read_text(encoding="utf-8")

        adapter = StrataAdapter()
        result = adapter.parse(html)

        for key, expected_val in EXPECTED.items():
            actual = result.get(key)
            assert actual == expected_val, (
                f"Field '{key}': expected {expected_val!r}, got {actual!r}"
            )

    def test_low_rsc_tax_uses_html_fallback(self):
        from listingtracker.adapters.strata import StrataAdapter

        adapter = StrataAdapter()
        html = (
            '<html><head></head><body>'
            '<script>self.__next_f.push([1,"'
            '\\"listPrice\\":337000,'
            '\\"bedroomCount\\":1,'
            '\\"taxesAnnual\\":52'
            '"])</script>'
            '<div>Taxes $4,120 (2026)</div>'
            '</body></html>'
        )
        result = adapter.parse(html)
        assert result["taxes_annual"] == 4120.0

    def test_detect_blocked(self):
        from listingtracker.adapters.strata import StrataAdapter

        adapter = StrataAdapter()
        assert adapter.detect_blocked("<html>Just a moment</html>") is True
        assert adapter.detect_blocked("<html>challenge-platform</html>") is True

    def test_not_blocked_normal_page(self):
        from listingtracker.adapters.strata import StrataAdapter

        adapter = StrataAdapter()
        html_path = FIXTURES_DIR / "strata_bayshore.html"
        html = html_path.read_text(encoding="utf-8")
        assert adapter.detect_blocked(html) is False


class TestAdapterNoWriteOutsideW:
    """Verify no file I/O targets anything outside W:\\ListingTracker."""

    def test_paths_all_under_root(self):
        from listingtracker.paths import ROOT, RUNTIME_DIRS, DB_PATH, CONFIG_PATH

        for d in RUNTIME_DIRS:
            assert str(d).startswith(str(ROOT)), f"{d} is not under {ROOT}"
        assert str(DB_PATH).startswith(str(ROOT))
        assert str(CONFIG_PATH).startswith(str(ROOT))

    def test_root_is_w_drive_in_dev(self):
        from listingtracker.paths import ROOT

        # In development (not frozen), ROOT is the W: drive checkout
        if not getattr(__import__("sys"), "frozen", False):
            assert str(ROOT).startswith("W:\\")


def test_clean_text_rejects_page_code_and_keeps_real_values():
    from listingtracker.adapters.base import clean_text, clean_parsed

    for junk in [
        'w-1/2 pl-2 lg:pl-0 text-[16px] leading-[26px] text-right lg:text-left">3',
        '":null,"stories":"1","fees":{"__typename":"Listing',
        '"https://cdn.wahi.com/listings/images/trreb/IMG-E12663382_1.jpg"}',
        '" class="hs-meta">',
        'KW Living Realty">Listed by KW Living Realty',
        '/css/dist/block-library/style.min.css?ver=7.1.4" media="all">',
        '_svg__a">',
        "   ",
    ]:
        assert clean_text(junk) is None, junk

    for real in ["1 space", "Owned", "Enclosed/Solarium", "30/60", "To Be Arranged", "West",
                 "KELLER WILLIAMS EMPOWERED REALTY", "RE/MAX HALLMARK REALTY LTD., BROKERAGE",
                 "Heat, Water, Building Insurance, Common Elements", "Condo Apartment"]:
        assert clean_text(real) == real, real

    assert clean_text(None) is None and clean_text(3) == 3
    parsed = clean_parsed({"broker": '" class="hs-meta">', "parking": "1 space", "address": "43 Taunton Rd E"})
    assert parsed == {"broker": None, "parking": "1 space", "address": "43 Taunton Rd E"}


def test_use_installed_browsers_picks_a_folder_that_exists(tmp_path):
    from listingtracker.paths import use_installed_browsers

    no_app_copy = tmp_path / "app-browsers"  # doesn't exist
    installed = tmp_path / "ms-playwright"
    installed.mkdir()

    env = {"LOCALAPPDATA": str(tmp_path)}
    use_installed_browsers(env, no_app_copy)
    assert env["PLAYWRIGHT_BROWSERS_PATH"] == str(installed)

    env = {"LOCALAPPDATA": str(tmp_path), "PLAYWRIGHT_BROWSERS_PATH": "0"}  # inherited from a launcher
    use_installed_browsers(env, no_app_copy)
    assert env["PLAYWRIGHT_BROWSERS_PATH"] == str(installed)

    custom = tmp_path / "custom"
    custom.mkdir()
    env = {"LOCALAPPDATA": str(tmp_path), "PLAYWRIGHT_BROWSERS_PATH": str(custom)}
    use_installed_browsers(env, no_app_copy)
    assert env["PLAYWRIGHT_BROWSERS_PATH"] == str(custom)  # a working setting is respected

    # the app's own copy wins whenever it is there
    app_copy = tmp_path / "app-copy"
    (app_copy / "chromium-1").mkdir(parents=True)
    use_installed_browsers(env, app_copy)
    assert env["PLAYWRIGHT_BROWSERS_PATH"] == str(app_copy)

    empty = tmp_path / "empty"
    empty.mkdir()
    env = {"LOCALAPPDATA": str(tmp_path)}
    use_installed_browsers(env, empty)
    assert env["PLAYWRIGHT_BROWSERS_PATH"] == str(installed)
