"""Data classes for listings and snapshots."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class Status(Enum):
    FOR_SALE = "FOR_SALE"
    SOLD_CONDITIONAL = "SOLD_CONDITIONAL"
    SOLD = "SOLD"
    TERMINATED = "TERMINATED"
    EXPIRED = "EXPIRED"
    DELISTED = "DELISTED"
    BLOCKED = "BLOCKED"
    UNKNOWN = "UNKNOWN"

    @classmethod
    def normalize(cls, raw: str) -> "Status":
        raw_upper = raw.strip().upper().replace(" ", "_").replace("-", "_")
        aliases = {
            "ACTIVE": cls.FOR_SALE,
            "FOR SALE": cls.FOR_SALE,
            "CONDITIONALLY_SOLD": cls.SOLD_CONDITIONAL,
            "SOLD_CONDITIONALLY": cls.SOLD_CONDITIONAL,
            "SC": cls.SOLD_CONDITIONAL,
            "SOLD_FIRM": cls.SOLD,
            "PC": cls.SOLD,
            "SUSPENDED": cls.DELISTED,
            "NOT_AVAILABLE": cls.DELISTED,
        }
        try:
            return cls(raw_upper)
        except ValueError:
            return aliases.get(raw_upper, cls.UNKNOWN)


@dataclass
class Listing:
    id: Optional[int] = None
    url: str = ""
    site: str = ""
    mls_number: Optional[str] = None
    address: str = ""
    unit: Optional[str] = None
    building_name: Optional[str] = None
    city: str = ""
    neighbourhood: Optional[str] = None
    date_added: Optional[datetime] = None
    active: bool = True
    user_notes: str = ""


@dataclass
class Snapshot:
    id: Optional[int] = None
    listing_id: int = 0
    fetched_at: Optional[datetime] = None
    status: Status = Status.UNKNOWN
    list_price: Optional[float] = None
    maintenance_fee: Optional[float] = None
    taxes_annual: Optional[float] = None
    beds: Optional[int] = None
    baths: Optional[int] = None
    sqft_min: Optional[int] = None
    sqft_max: Optional[int] = None
    parking: Optional[str] = None
    locker: Optional[str] = None
    balcony: Optional[str] = None
    exposure: Optional[str] = None
    days_on_market: Optional[int] = None
    site_estimate: Optional[float] = None
    broker: Optional[str] = None
    fee_includes: Optional[str] = None
    property_type: Optional[str] = None
    year_built: Optional[int] = None
    ensuite_laundry: Optional[str] = None
    outdoor_space: Optional[str] = None
    possession_date: Optional[str] = None
    rent_controlled: Optional[str] = None
    power_of_sale: Optional[bool] = None
    raw_html_path: Optional[str] = None
    parse_errors: Optional[str] = None


@dataclass
class Change:
    listing_id: int
    listing: Listing
    field: str
    old_value: object
    new_value: object
    severity: str = "info"  # "info", "warning", "critical"
    description: str = ""
