"""Local-area news for a listing: crime, fires, deaths and disruptions reported nearby (Google News RSS)."""

from __future__ import annotations

import logging
import re
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Optional

from listingtracker import geo

logger = logging.getLogger(__name__)

FEED_URL = "https://news.google.com/rss/search"

# Most specific first
SCOPES = ("street", "neighbourhood", "city")
SCOPE_WINDOW = {"street": "5y", "neighbourhood": "2y", "city": "1y"}
SCOPE_LIMIT = {"street": 10, "neighbourhood": 8, "city": 8}

INCIDENT_TERMS = ('(shooting OR stabbing OR homicide OR murder OR assault OR robbery OR "break-in" '
                  "OR arson OR fire OR fatal OR death)")

# Most severe first: an article matching several is filed under the first that matches
CATEGORIES: list[tuple[str, str, re.Pattern]] = [
    ("violent", "Violent crime", re.compile(
        r"\b(shooting|shots?|gunfire|gunman|gunpoint|stabb\w*|homicide|murder\w*|manslaughter|assault\w*|"
        r"attack\w*|armed|kidnap\w*|abduct\w*|home invasion|threats?|threaten\w*)\b", re.I)),
    ("theft", "Robbery & theft", re.compile(
        r"\b(robber\w*|robs?|robbed|break-ins?|break[- ]and[- ]enter\w*|burglar\w*|thefts?|thie(?:f|ves)|"
        r"stolen|carjack\w*)\b", re.I)),
    ("fire", "Fire", re.compile(r"\b(fires?|blaze|arson\w*|explosion)\b", re.I)),
    ("death", "Death", re.compile(
        r"\b(dead|deaths?|dies|died|fatal\w*|killed|body found|overdos\w*|drown\w*)\b", re.I)),
    ("disruption", "Roads & hazards", re.compile(
        r"\b(closures?|construction|lane restrictions?|rezoning|flood\w*|sinkhole|gas leak|evacuat\w*|"
        r"contaminat\w*|boil[- ]water|power outage)\b", re.I)),
]
CATEGORY_LABELS = {key: label for key, label, _pattern in CATEGORIES}
_SEVERITY = {key: rank for rank, (key, _label, _pattern) in enumerate(CATEGORIES)}

# Headlines that trip an incident keyword without being an incident
NOT_AN_INCIDENT = re.compile(
    r"\b(fireworks|safety tips|fire ban|open house|recruit\w*|fundrais\w*|fire sale|on fire|"
    r"goals?|league|striker|playoffs?|championship|tournament|films?|filmed|filming|premieres?)\b", re.I)

STREET_TYPES = {
    "st": "Street", "rd": "Road", "ave": "Avenue", "av": "Avenue", "dr": "Drive", "blvd": "Boulevard",
    "cres": "Crescent", "crt": "Court", "ct": "Court", "ln": "Lane", "pl": "Place", "pkwy": "Parkway",
    "hwy": "Highway", "terr": "Terrace", "cir": "Circle", "sq": "Square", "tr": "Trail", "gdns": "Gardens",
}
_DIRECTIONS = {"n", "s", "e", "w", "north", "south", "east", "west"}

_STOPWORDS = {
    "after", "with", "from", "that", "this", "police", "says", "over", "into", "near", "following",
    "been", "were", "have", "their", "ontario", "durham", "regional", "service", "about", "more",
    "than", "will", "what", "when", "where", "which", "investigate", "investigating", "investigation",
}


@dataclass
class NewsItem:
    title: str
    url: str
    source: str
    published: Optional[datetime]
    category: str
    scope: str


@dataclass
class Incident:
    """One event, possibly covered by several outlets."""
    title: str
    url: str
    source: str
    published: Optional[datetime]
    category: str
    scope: str
    reports: int = 1


@dataclass
class AreaNews:
    scope_names: dict[str, str] = field(default_factory=dict)  # searched scope -> place name
    incidents: list[Incident] = field(default_factory=list)
    failed: bool = False  # no feed could be reached

    def for_scope(self, scope: str) -> list[Incident]:
        return [i for i in self.incidents if i.scope == scope]


def street_names(address: str) -> list[str]:
    """Spellings of a listing's street to search for, e.g. ['Dooley Crescent', 'Dooley Cres']."""
    words = (address or "").split(",")[0].split()
    while words and any(ch.isdigit() for ch in words[0]):
        words.pop(0)  # house and unit numbers
    while words and words[-1].lower().strip(".") in _DIRECTIONS:
        words.pop()
    if not words:
        return []
    written = " ".join(words)
    full_type = STREET_TYPES.get(words[-1].lower().strip("."))
    if full_type and len(words) > 1:
        return [" ".join(words[:-1] + [full_type]), written]
    return [written]


def build_queries(address: str, city: str, neighbourhood: Optional[str]) -> list[tuple[str, str, str]]:
    """(scope, place name, search query) for each level that can be searched."""
    city = (city or "").strip().title()
    queries = []
    streets = street_names(address)
    if streets and city:
        spellings = " OR ".join(f'"{name}"' for name in streets)
        queries.append(("street", streets[0], f"({spellings}) {city} when:{SCOPE_WINDOW['street']}"))
    hood = (neighbourhood or "").strip()
    if city and 3 <= len(hood) <= 40 and hood.lower() != city.lower() and re.fullmatch(r"[\w .'&-]+", hood):
        queries.append(("neighbourhood", hood,
                        f'"{hood}" {city} Ontario {INCIDENT_TERMS} when:{SCOPE_WINDOW["neighbourhood"]}'))
    if city:
        queries.append(("city", city, f"{city} Ontario {INCIDENT_TERMS} when:{SCOPE_WINDOW['city']}"))
    return queries


def categorize(title: str) -> Optional[str]:
    if NOT_AN_INCIDENT.search(title):
        return None
    for key, _label, pattern in CATEGORIES:
        if pattern.search(title):
            return key
    return None


_PERSON = (r"(?:man|men|woman|women|residents?|teens?|teenagers?|youths?|boy|girl|driver|senior|"
           r"couple|pair|native|mother|father|family|student)")


# Capitalised words after "in"/"near"/"at" that don't move an event out of the city
_NOT_ANOTHER_PLACE = {
    "Durham", "Ontario", "Canada", "Highway", "Hwy", "Lake", "January", "February", "March", "April",
    "May", "June", "July", "August", "September", "October", "November", "December",
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
}


def happened_elsewhere(title: str, city: str) -> bool:
    """True for headlines like "Oshawa man charged in Cobourg": the city is only where someone
    is from, and the event is pinned to another place."""
    mentions = list(re.finditer(rf"\b{re.escape(city)}\b(?:\s+(?:area\s+)?{_PERSON}\b)?", title, re.I))
    if not mentions or any(m.group(0).lower() == city.lower() for m in mentions):
        return False  # the city is named on its own somewhere, so the event may well be there
    words = re.findall(r"[A-Za-z]{4,}", title)
    if words and sum(w[0].isupper() for w in words) / len(words) > 0.7:
        return False  # Title Case Headline: capitals don't single out place names
    return any(place.lower() != city.lower() and place not in _NOT_ANOTHER_PLACE
               for place in re.findall(r"\b(?:in|near|at)\s+([A-Z][a-z]+)", title))


def parse_feed(xml: bytes, scope: str) -> list[NewsItem]:
    """Incident headlines from one RSS feed; anything that isn't an incident is dropped."""
    items = []
    for node in ET.fromstring(xml).findall("./channel/item"):
        title = (node.findtext("title") or "").strip()
        source = (node.findtext("source") or "").strip()
        if source and title.endswith(f" - {source}"):
            title = title[: -len(source) - 3]
        url = (node.findtext("link") or "").strip()
        category = categorize(title)
        if not title or not url.startswith("https://") or category is None:
            continue
        try:
            published = parsedate_to_datetime(node.findtext("pubDate") or "").replace(tzinfo=None)
        except (TypeError, ValueError):
            published = None
        items.append(NewsItem(title, url, source, published, category, scope))
    return items


def _words(title: str, ignore: set[str]) -> set[str]:
    found = re.findall(r"[a-z0-9][a-z0-9'-]+", title.lower())
    return {w for w in found if len(w) > 3 and w not in _STOPWORDS and w not in ignore}


def group_incidents(items: list[NewsItem], place_words: set[str]) -> list[Incident]:
    """Merge different outlets' reports of the same event so one event counts once."""
    seen_titles: set[str] = set()
    unique = []
    for item in sorted(items, key=lambda i: SCOPES.index(i.scope)):
        if item.title.lower() not in seen_titles:
            seen_titles.add(item.title.lower())
            unique.append(item)

    clusters: list[list[tuple[NewsItem, set[str]]]] = []
    for item in sorted(unique, key=lambda i: i.published or datetime.min):
        words = _words(item.title, place_words)
        home = None
        if item.published:
            for cluster in clusters:
                if any(other.published and abs(item.published - other.published) <= timedelta(days=2)
                       and len(words & other_words) >= 2 for other, other_words in cluster):
                    home = cluster
                    break
        if home is None:
            clusters.append([(item, words)])
        else:
            home.append((item, words))

    incidents = []
    for cluster in clusters:
        members = [item for item, _ in cluster]
        lead = min(members, key=lambda i: (SCOPES.index(i.scope), i.published or datetime.min))
        incidents.append(Incident(
            title=lead.title, url=lead.url, source=lead.source, published=lead.published,
            category=min((m.category for m in members), key=_SEVERITY.get),
            scope=lead.scope, reports=len(members),
        ))
    return incidents


def fetch_area_news(address: str, city: str, neighbourhood: Optional[str],
                    now: Optional[datetime] = None) -> AreaNews:
    """Search local news around a listing. Blocking — call off the UI thread."""
    news = AreaNews()
    found: list[NewsItem] = []
    reached = False
    for scope, name, query in build_queries(address, city, neighbourhood):
        news.scope_names[scope] = name
        params = urllib.parse.urlencode({"q": query, "hl": "en-CA", "gl": "CA", "ceid": "CA:en"})
        try:
            found.extend(parse_feed(geo.http_get(f"{FEED_URL}?{params}"), scope))
            reached = True
        except Exception as e:
            logger.info("Area news search failed for %s %s: %s", scope, name, e)

    city_name = (city or "").strip().title()
    hood = news.scope_names.get("neighbourhood", "")
    year_ago = (now or datetime.now()) - timedelta(days=366)
    items: list[NewsItem] = []
    for item in found:
        if item.scope == "neighbourhood" and hood.lower() not in item.title.lower():
            # The search also returns city stories that mention the neighbourhood's name only in
            # passing (or mean something else by it). Those belong with the city's news, which
            # covers the past year.
            if item.published and item.published < year_ago:
                continue
            item.scope = "city"
        if item.scope == "city":
            # City-wide searches match loosely; keep headlines about something in the city
            if city_name.lower() not in item.title.lower() or happened_elsewhere(item.title, city_name):
                continue
        items.append(item)

    news.failed = bool(news.scope_names) and not reached
    place_words = {w.lower() for name in news.scope_names.values() for w in name.split()}
    by_scope: dict[str, list[Incident]] = {scope: [] for scope in SCOPES}
    for incident in group_incidents(items, place_words):
        by_scope[incident.scope].append(incident)
    for scope in SCOPES:
        newest_first = sorted(by_scope[scope], key=lambda i: i.published or datetime.min, reverse=True)
        news.incidents.extend(newest_first[: SCOPE_LIMIT[scope]])
    return news
