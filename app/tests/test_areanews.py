"""Tests for the local-area news search (feed responses mocked)."""

from datetime import datetime
from unittest.mock import patch

from listingtracker import areanews
from listingtracker.areanews import NewsItem


def _feed(*items):
    body = "".join(
        f"<item><title>{title} - {source}</title><link>https://news.example/{n}</link>"
        f"<pubDate>{date}</pubDate><source url='https://x'>{source}</source></item>"
        for n, (title, source, date) in enumerate(items)
    )
    return f"<rss><channel>{body}</channel></rss>".encode("utf-8")


def test_street_names_expand_abbreviations_and_drop_numbers():
    assert areanews.street_names("157 Dooley Cres") == ["Dooley Crescent", "Dooley Cres"]
    assert areanews.street_names("100 King Street East") == ["King Street"]
    assert areanews.street_names("1604-1614 Charles Street, Whitby") == ["Charles Street"]
    assert areanews.street_names("44 Bond St W") == ["Bond Street", "Bond St"]
    assert areanews.street_names("") == []
    assert areanews.street_names("320") == []


def test_build_queries_covers_available_levels():
    queries = areanews.build_queries("157 Dooley Cres", "ajax", "Duffins Bay")
    assert [scope for scope, _name, _q in queries] == ["street", "neighbourhood", "city"]
    street_query = queries[0][2]
    assert '"Dooley Crescent" OR "Dooley Cres"' in street_query and "Ajax" in street_query
    assert '"Duffins Bay"' in queries[1][2]

    # no street, and a neighbourhood that just repeats the city
    assert [scope for scope, _n, _q in areanews.build_queries("", "Whitby", "whitby")] == ["city"]
    assert areanews.build_queries("12 Main St", "", None) == []


def test_categorize_picks_most_severe_and_skips_lookalikes():
    assert areanews.categorize("Man dead after shooting in Oshawa") == "violent"
    assert areanews.categorize("Youth Stabbed During Robbery in Whitby") == "violent"
    assert areanews.categorize("Thieves use excavator to rob bank") == "theft"
    assert areanews.categorize("Fatal house fire under investigation") == "fire"
    assert areanews.categorize("Fatal collision involving pedestrian") == "death"
    assert areanews.categorize("Lane restrictions coming to Simcoe Street") == "disruption"
    assert areanews.categorize("What to do on March Break in Durham Region") is None
    assert areanews.categorize("Ajax Fire reminds residents of fireworks safety tips") is None
    assert areanews.categorize("Striker on fire as Ajax win 6-1") is None
    assert areanews.categorize("New feature film shot in Ajax premieres Nov. 26") is None
    assert areanews.categorize("Man shot in Whitby parking lot") == "violent"
    assert areanews.categorize("New bakery opens downtown") is None


def test_parse_feed_strips_source_suffix_and_drops_non_incidents():
    xml = _feed(
        ("Shooting at car dealership in Whitby", "CP24", "Tue, 25 Aug 2026 14:00:00 GMT"),
        ("Farmers market returns this weekend", "Durham Post", "Tue, 25 Aug 2026 14:00:00 GMT"),
    )
    items = areanews.parse_feed(xml, "city")
    assert len(items) == 1
    assert items[0].title == "Shooting at car dealership in Whitby"
    assert items[0].source == "CP24"
    assert items[0].published == datetime(2026, 8, 25, 14, 0)
    assert items[0].category == "violent" and items[0].scope == "city"


def test_same_event_from_several_outlets_counts_once():
    day = datetime(2026, 8, 25)
    items = [
        NewsItem("One wanted after drive-by shooting in Ajax", "u1", "Radio", day, "violent", "street"),
        NewsItem("Suspects sought following drive-by shooting in Ajax", "u2", "Quinte", day, "violent", "city"),
        NewsItem("Police investigate drive-by shooting in Ajax", "u3", "RED FM", datetime(2026, 8, 26), "violent", "city"),
        NewsItem("One wanted after drive-by shooting in Ajax", "u4", "Mirror", day, "violent", "city"),  # exact repeat
        NewsItem("Drive-by shooting damages home in Ajax", "u5", "CP24", datetime(2026, 3, 1), "violent", "city"),
        NewsItem("Garage fire on quiet Ajax street", "u6", "CP24", day, "fire", "city"),
    ]
    incidents = areanews.group_incidents(items, place_words={"ajax"})
    by_title = {i.title: i for i in incidents}
    assert len(incidents) == 3
    merged = by_title["One wanted after drive-by shooting in Ajax"]
    assert merged.reports == 3
    assert merged.scope == "street"  # the most specific sighting wins
    assert by_title["Drive-by shooting damages home in Ajax"].reports == 1  # months apart: separate event
    assert by_title["Garage fire on quiet Ajax street"].category == "fire"


def test_fetch_area_news_assigns_scopes_and_filters_city_results():
    def fake_get(url):
        if "Dooley" in url:
            return _feed(("Home damaged in drive-by shooting", "CP24", "Tue, 25 Aug 2026 14:00:00 GMT"))
        return _feed(
            ("Ajax arson: suspect facing 14 charges", "CP24", "Wed, 30 Sep 2026 10:00:00 GMT"),
            ("Ontario man who shot at intruders loses appeal", "CTV", "Thu, 01 Oct 2026 10:00:00 GMT"),
        )

    with patch("listingtracker.geo.http_get", side_effect=fake_get):
        news = areanews.fetch_area_news("157 Dooley Cres", "Ajax", None)

    assert not news.failed
    assert news.scope_names == {"street": "Dooley Crescent", "city": "Ajax"}
    assert [i.title for i in news.for_scope("street")] == ["Home damaged in drive-by shooting"]
    # the city-wide headline that never names Ajax is dropped
    assert [i.title for i in news.for_scope("city")] == ["Ajax arson: suspect facing 14 charges"]


def test_fetch_area_news_reports_failure_only_when_nothing_was_reachable():
    with patch("listingtracker.geo.http_get", side_effect=OSError("offline")):
        news = areanews.fetch_area_news("157 Dooley Cres", "Ajax", None)
    assert news.failed and news.incidents == []

    calls = []

    def flaky(url):
        calls.append(url)
        if len(calls) == 1:
            raise OSError("timeout")
        return _feed()

    with patch("listingtracker.geo.http_get", side_effect=flaky):
        news = areanews.fetch_area_news("157 Dooley Cres", "Ajax", None)
    assert not news.failed and news.incidents == []


def test_threats_are_violent_crime_not_a_death():
    assert areanews.categorize("Man charged with uttering death threats") == "violent"
    assert areanews.categorize("Woman threatened with knife outside store") == "violent"


def test_happened_elsewhere_spots_events_pinned_to_another_place():
    elsewhere = areanews.happened_elsewhere
    assert elsewhere("Refused service, Oshawa man charged with uttering death threats in Cobourg, Ontario", "Oshawa")
    assert elsewhere("Oshawa woman killed in Toronto crash", "Oshawa")
    assert not elsewhere("Oshawa man shot in driveway", "Oshawa")
    assert not elsewhere("Oshawa man charged after stabbing in Oshawa", "Oshawa")
    assert not elsewhere("Shooting in Oshawa sends man to hospital in Toronto", "Oshawa")
    assert not elsewhere("Oshawa driver dies in Highway 401 crash", "Oshawa")
    # Title Case headlines capitalise everything, so capitals can't single out a place
    assert not elsewhere("Oshawa Man Arrested in Firearm Investigation", "Oshawa")


def test_neighbourhood_results_must_name_the_neighbourhood():
    def fake_get(url):
        if "Centennial" in url:
            return _feed(
                ("Stabbing in Oshawa's Centennial neighbourhood", "CTV", "Tue, 10 Nov 2026 14:00:00 GMT"),
                ("Police investigating homicide in Oshawa", "CTV", "Sat, 09 May 2026 14:00:00 GMT"),
                ("Man in life-threatening condition after Oshawa shooting", "CTV", "Mon, 22 Dec 2024 14:00:00 GMT"),
                ("Sweatshirt robbery and assault prompts police search in Ontario town", "insauga", "Fri, 28 Aug 2026 14:00:00 GMT"),
            )
        return _feed(
            ("Oshawa man charged with uttering death threats in Cobourg", "insauga", "Tue, 10 Nov 2026 10:00:00 GMT"),
            ("Fire at Oshawa home causes minor injuries", "Durham Radio", "Mon, 09 Nov 2026 10:00:00 GMT"),
        )

    with patch("listingtracker.geo.http_get", side_effect=fake_get):
        news = areanews.fetch_area_news("", "Oshawa", "Centennial", now=datetime(2026, 11, 11))

    assert [i.title for i in news.for_scope("neighbourhood")] == ["Stabbing in Oshawa's Centennial neighbourhood"]
    # City stories the neighbourhood search dragged in are filed under the city, when they are
    # recent and name it; the Cobourg story is dropped
    assert [i.title for i in news.for_scope("city")] == [
        "Fire at Oshawa home causes minor injuries",
        "Police investigating homicide in Oshawa",
    ]


def test_city_results_are_capped_newest_first():
    places = ["bank", "pharmacy", "jewellery store", "gas station", "bakery", "arena", "library",
              "marina", "cinema", "diner", "florist", "bookshop"]
    items = [(f"Robbery at Ajax {place}", "DRPS", f"{1 + n:02d} Sep 2026 10:00:00 GMT")
             for n, place in enumerate(places)]
    with patch("listingtracker.geo.http_get", return_value=_feed(*items)):
        news = areanews.fetch_area_news("", "Ajax", None)
    city = news.for_scope("city")
    assert len(city) == areanews.SCOPE_LIMIT["city"]
    assert city[0].published > city[-1].published
