"""Cross-reference listings across different websites to verify data."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Optional

from listingtracker.adapters.base import clean_text
from listingtracker.models import Listing, Snapshot

logger = logging.getLogger(__name__)


@dataclass
class FieldComparison:
    field: str
    label: str
    values: dict[str, object]  # site → value
    consensus: Optional[object] = None
    agreed: bool = False


@dataclass
class CrossRefResult:
    group_id: int
    listings: list[Listing]
    snapshots: dict[int, Snapshot]  # listing_id → latest snapshot
    comparisons: list[FieldComparison] = field(default_factory=list)
    match_reason: str = ""


def _normalize_address(addr: str) -> str:
    """Normalize an address for comparison: lowercase, strip punctuation, collapse whitespace."""
    if not addr:
        return ""
    addr = addr.lower().strip()
    addr = re.sub(r'[.,#\-]', ' ', addr)
    addr = re.sub(r'\s+', ' ', addr)
    addr = _NORMALIZE_RE.sub(lambda m: _STREET_TYPE_NORMALIZE[m.group(0)], addr)
    return addr.strip()


def _normalize_unit(unit: Optional[str]) -> str:
    if not unit:
        return ""
    return re.sub(r'[^a-z0-9]', '', unit.lower().strip())


def _normalize_city(city: str) -> str:
    if not city:
        return ""
    return city.lower().strip()


DIRECTIONS = {"n", "s", "e", "w", "north", "south", "east", "west"}

_STREET_TYPE_NORMALIZE = {
    "street": "st", "avenue": "ave", "drive": "dr", "road": "rd",
    "boulevard": "blvd", "crescent": "cres", "court": "ct", "lane": "ln",
    "terrace": "ter", "place": "pl",
    "crt": "ct", "terr": "ter", "av": "ave",
    "circle": "cir", "parkway": "pkwy", "trail": "tr",
    "highway": "hwy", "square": "sq", "gardens": "gdns",
}

STREET_ABBREV_TO_FULL = {
    "st": "Street", "rd": "Road", "ave": "Avenue", "dr": "Drive",
    "blvd": "Boulevard", "cres": "Crescent", "ct": "Court", "ln": "Lane",
    "pl": "Place", "ter": "Terrace", "cir": "Circle", "pkwy": "Parkway",
    "hwy": "Highway", "sq": "Square", "tr": "Trail", "gdns": "Gardens",
}

_NORMALIZE_RE = re.compile(
    r'\b(' + '|'.join(sorted(_STREET_TYPE_NORMALIZE, key=len, reverse=True)) + r')\b'
)
_UNIT_WORDS = {"unit", "apt", "apartment", "suite", "ste", "ph"}


def _street_identity(address: str) -> tuple[Optional[str], list[str], list[str]]:
    """(street number, street-name words, unit candidates) from a written address.

    "36 - 43 Taunton Rd E, Oshawa" -> ("43", ["taunton", "rd", "oshawa"], ["36"]). The name keeps any
    trailing words such as the city; callers compare names by prefix.
    """
    tokens = _normalize_address(address or "").split()
    units: list[str] = []
    # "unit 36 43 taunton rd" / "43 taunton rd unit 36": lift the labelled unit out
    for i, tok in enumerate(tokens[:-1]):
        if tok in _UNIT_WORDS:
            units.append(tokens[i + 1])
            tokens = tokens[:i] + tokens[i + 2:]
            break
    # The street name starts at the first word that isn't a number ("3rd" counts as a word)
    start = next((i for i, tok in enumerate(tokens)
                  if not tok[0].isdigit() or (i > 0 and re.fullmatch(r"\d+(st|nd|rd|th)", tok))),
                 len(tokens))
    leading = tokens[:start]
    number = leading[-1] if leading else None
    units.extend(leading[:-1])  # "36 43 taunton" -> unit 36 at number 43
    name = [_STREET_TYPE_NORMALIZE.get(tok, tok) for tok in tokens[start:] if tok not in DIRECTIONS]
    return number, name, [_normalize_unit(u) for u in units]


def is_same_property(listing: Listing, parsed: dict) -> bool:
    """Whether data parsed from another site's page describes this listing's property.

    A search can land on a neighbouring unit or an unrelated listing altogether, so a page only
    counts when its MLS number matches, or its street number and name match and (for a listing
    with a unit) the unit does too. Anything that can't be confirmed is treated as a mismatch.
    """
    ours = (listing.mls_number or "").strip().upper()
    theirs = (parsed.get("mls_number") or "").strip().upper()
    if ours and theirs:
        return ours == theirs

    number, name, _own_units = _street_identity(listing.address)
    their_number, their_name, their_units = _street_identity(parsed.get("address") or "")
    if not number or not name or number != their_number:
        return False
    shared = min(len(name), len(their_name))
    if shared == 0 or name[:shared] != their_name[:shared]:
        return False

    unit = _normalize_unit(listing.unit)
    if unit:
        if parsed.get("unit"):
            their_units = their_units + [_normalize_unit(str(parsed["unit"]))]
        return unit in their_units
    return True


def rank_candidate_urls(urls: list[str], listing: Listing) -> list[str]:
    """Search-result links ordered so the ones that look like this listing come first."""
    number, name, _units = _street_identity(listing.address)
    slug = "-".join([number] + name[:1]) if number and name else ""
    mls = (listing.mls_number or "").strip().lower()
    unit = _normalize_unit(listing.unit)

    def score(url: str) -> int:
        path = url.lower()
        points = 0
        if mls and mls in path:
            points += 4
        if slug and re.search(rf"(?<!\d){re.escape(slug)}", path):
            points += 2
            if unit and re.search(rf"(?<![a-z0-9]){re.escape(unit)}(?![a-z0-9])", path.replace(slug, "")):
                points += 1
        return points

    return sorted(sorted(urls), key=score, reverse=True)


def find_cross_ref_groups(
    listings: list[Listing],
    snapshots: dict[int, Optional[Snapshot]],
) -> list[list[int]]:
    """Group listing IDs that refer to the same physical property.

    Matching priority:
    1. Same MLS number (strongest signal)
    2. Same normalized address + unit + city
    """
    # Union-Find for grouping
    parent: dict[int, int] = {lst.id: lst.id for lst in listings}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    # Index by MLS number
    mls_index: dict[str, list[int]] = {}
    for lst in listings:
        if lst.mls_number:
            key = lst.mls_number.strip().upper()
            mls_index.setdefault(key, []).append(lst.id)

    for ids in mls_index.values():
        for i in range(1, len(ids)):
            union(ids[0], ids[i])

    # Index by normalized address + unit + city
    addr_index: dict[tuple[str, str, str], list[int]] = {}
    for lst in listings:
        if not lst.address:
            continue
        key = (_normalize_address(lst.address),
               _normalize_unit(lst.unit),
               _normalize_city(lst.city))
        if key[0]:  # must have an address
            addr_index.setdefault(key, []).append(lst.id)

    for ids in addr_index.values():
        if len(ids) > 1:
            # Only merge listings from DIFFERENT sites
            sites_seen: dict[str, int] = {}
            for lid in ids:
                lst = next(l for l in listings if l.id == lid)
                if lst.site not in sites_seen:
                    sites_seen[lst.site] = lid
            # Now merge across sites
            site_reps = list(sites_seen.values())
            for i in range(1, len(site_reps)):
                union(site_reps[0], site_reps[i])

    # Collect groups (only groups with 2+ listings from different sites)
    groups_map: dict[int, list[int]] = {}
    for lst in listings:
        root = find(lst.id)
        groups_map.setdefault(root, []).append(lst.id)

    result = []
    for group_ids in groups_map.values():
        if len(group_ids) < 2:
            continue
        # Verify at least 2 different sites
        group_listings = [l for l in listings if l.id in group_ids]
        sites = {l.site for l in group_listings}
        if len(sites) >= 2:
            result.append(sorted(group_ids))

    return result


COMPARED_FIELDS = [
    ("list_price", "Price"),
    ("beds", "Beds"),
    ("baths", "Baths"),
    ("sqft_min", "Sqft (min)"),
    ("sqft_max", "Sqft (max)"),
    ("maintenance_fee", "Maint. Fee"),
    ("taxes_annual", "Annual Taxes"),
    ("parking", "Parking"),
    ("locker", "Locker"),
    ("exposure", "Exposure"),
    ("status", "Status"),
    ("broker", "Broker"),
]


def compare_group(
    group_listings: list[Listing],
    snapshots: dict[int, Snapshot],
) -> list[FieldComparison]:
    """Compare field values across listings in a cross-ref group."""
    comparisons = []

    for field_name, label in COMPARED_FIELDS:
        values: dict[str, object] = {}
        for lst in group_listings:
            snap = snapshots.get(lst.id)
            if not snap:
                continue
            if field_name == "status":
                val = snap.status.value if hasattr(snap.status, 'value') else str(snap.status)
            else:
                val = getattr(snap, field_name, None)
            if val is not None:
                values[lst.site] = val

        if not values:
            continue

        # Determine consensus
        unique_vals = set()
        for v in values.values():
            if isinstance(v, float):
                unique_vals.add(round(v, 2))
            elif isinstance(v, str):
                unique_vals.add(v.strip().lower())
            else:
                unique_vals.add(v)

        agreed = len(unique_vals) <= 1
        consensus = list(values.values())[0] if agreed and values else None

        comparisons.append(FieldComparison(
            field=field_name,
            label=label,
            values=values,
            consensus=consensus,
            agreed=agreed,
        ))

    return comparisons


def build_cross_ref_results(
    listings: list[Listing],
    snapshots_map: dict[int, Optional[Snapshot]],
) -> list[CrossRefResult]:
    """Run full cross-reference: find groups and compare fields."""
    groups = find_cross_ref_groups(listings, snapshots_map)
    results = []

    for i, group_ids in enumerate(groups):
        group_listings = [l for l in listings if l.id in group_ids]
        group_snaps = {}
        for lid in group_ids:
            snap = snapshots_map.get(lid)
            if snap:
                group_snaps[lid] = snap

        # Determine match reason
        mls_set = {l.mls_number.strip().upper() for l in group_listings
                    if l.mls_number}
        if len(mls_set) == 1:
            reason = f"MLS# {mls_set.pop()}"
        else:
            addr = group_listings[0].address or "?"
            unit = group_listings[0].unit
            reason = f"{addr}"
            if unit:
                reason += f" Unit {unit}"

        comparisons = compare_group(group_listings, group_snaps)

        results.append(CrossRefResult(
            group_id=i + 1,
            listings=group_listings,
            snapshots=group_snaps,
            comparisons=comparisons,
            match_reason=reason,
        ))

    return results


def get_cross_ref_for_listing(
    listing_id: int,
    all_results: list[CrossRefResult],
) -> Optional[CrossRefResult]:
    """Find the cross-ref group that contains a given listing."""
    for result in all_results:
        if any(l.id == listing_id for l in result.listings):
            return result
    return None


ENRICHABLE_FIELDS = [
    ("list_price", "Price"),
    ("maintenance_fee", "Maint. Fee"),
    ("taxes_annual", "Annual Taxes"),
    ("beds", "Beds"),
    ("baths", "Baths"),
    ("sqft_min", "Sqft (min)"),
    ("sqft_max", "Sqft (max)"),
    ("parking", "Parking"),
    ("locker", "Locker"),
    ("exposure", "Exposure"),
    ("days_on_market", "Days on Market"),
    ("broker", "Broker"),
    ("fee_includes", "Fee Includes"),
    ("property_type", "Property Type"),
    ("year_built", "Year Built"),
    ("ensuite_laundry", "Ensuite Laundry"),
    ("outdoor_space", "Outdoor Space"),
    ("possession_date", "Possession Date"),
    ("rent_controlled", "Rent Controlled"),
]


@dataclass
class EnrichmentItem:
    """A single field found on another site that the current listing is missing or differs on."""
    field: str
    label: str
    current_value: Optional[object]
    found_value: object
    source_site: str
    source_url: str = ""


@dataclass
class EnrichmentResult:
    """Results from enrichment: fields found on other sites."""
    listing_id: int
    items: list[EnrichmentItem] = field(default_factory=list)
    sites_searched: list[str] = field(default_factory=list)
    sites_found: list[str] = field(default_factory=list)


def merge_from_cross_ref(
    listing_id: int,
    snap: Optional["Snapshot"],
    xref: Optional[CrossRefResult],
) -> list[EnrichmentItem]:
    """Extract missing fields from cross-ref matches (instant, no browser)."""
    if not xref or not snap:
        return []

    items: list[EnrichmentItem] = []
    other_listings = [l for l in xref.listings if l.id != listing_id]
    if not other_listings:
        return []

    for field_name, label in ENRICHABLE_FIELDS:
        current = getattr(snap, field_name, None)
        if current is not None:
            continue

        for ol in other_listings:
            ol_snap = xref.snapshots.get(ol.id)
            if not ol_snap:
                continue
            other_val = getattr(ol_snap, field_name, None)
            if other_val is not None:
                items.append(EnrichmentItem(
                    field=field_name,
                    label=label,
                    current_value=current,
                    found_value=other_val,
                    source_site=ol.site,
                    source_url=ol.url,
                ))
                break

    return items


def build_enrichment_search_urls(listing: "Listing") -> list[dict]:
    """Build site-specific search URLs for finding this listing on other sites."""
    from urllib.parse import quote, quote_plus

    urls: list[dict] = []
    source = listing.site or ""
    mls = listing.mls_number or ""
    addr = listing.address or ""
    city = (listing.city or "").lower().strip()
    unit = listing.unit or ""

    if not addr and not mls:
        return urls

    query = mls if mls else f"{addr} {unit}".strip()

    if source != "housesigma.com":
        hs_url = f"https://housesigma.com/web/en/map?search_text={quote_plus(query)}"
        if city:
            hs_url += f"&municipality={quote(city.title())}"
        urls.append({
            "site": "housesigma.com",
            "url": hs_url,
            "description": f"housesigma.com — {query}",
        })

    if source != "strata.ca" and city:
        city_slug = city.replace(" ", "-")
        urls.append({
            "site": "strata.ca",
            "url": f"https://strata.ca/{city_slug}/condos-for-sale?search={quote_plus(query)}",
            "description": f"strata.ca — {query}",
        })

    if source != "zolo.ca":
        urls.append({
            "site": "zolo.ca",
            "url": f"https://www.zolo.ca/{city or 'ontario'}-real-estate?sarea={quote_plus(query)}",
            "description": f"zolo.ca — {query}",
        })

    if source != "condos.ca" and city:
        urls.append({
            "site": "condos.ca",
            "url": f"https://condos.ca/{city}/condos-for-sale?search={quote_plus(query)}",
            "description": f"condos.ca — {query}",
        })

    return urls


def build_enrichment_from_parsed(
    listing_id: int,
    snap: Optional["Snapshot"],
    site: str,
    url: str,
    parsed: dict,
) -> list[EnrichmentItem]:
    """Compare parsed data from another site against current snapshot, return missing/different fields."""
    items: list[EnrichmentItem] = []
    if not snap:
        return items

    for field_name, label in ENRICHABLE_FIELDS:
        current = getattr(snap, field_name, None)
        found = clean_text(parsed.get(field_name))

        if found is None:
            continue

        if current is None:
            items.append(EnrichmentItem(
                field=field_name, label=label,
                current_value=current, found_value=found,
                source_site=site, source_url=url,
            ))
        elif _values_differ(current, found):
            items.append(EnrichmentItem(
                field=field_name, label=label,
                current_value=current, found_value=found,
                source_site=site, source_url=url,
            ))

    return items


def _values_differ(a, b) -> bool:
    """Check if two field values are meaningfully different."""
    if a is None or b is None:
        return a != b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(float(a) - float(b)) > 0.01
    return str(a).strip().lower() != str(b).strip().lower()
