"""Buyer profile system — swappable profiles with search criteria and buffer settings."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

import yaml

from listingtracker.finance import FinanceConfig
from listingtracker.paths import PROFILES_DIR, CONFIG_PATH

logger = logging.getLogger(__name__)

_FC = FinanceConfig()

# (stored key, label shown in the UI)
PROPERTY_TYPE_CHOICES = [
    ("condo", "Condo / apartment"),
    ("townhouse", "Townhouse"),
    ("house", "Detached / semi-detached house"),
]


@dataclass
class BuyerProfile:
    name: str = "Default"

    # --- Identity ---
    buyer_name: str = ""
    employer: str = ""
    employment_start: str = ""
    notes: str = ""

    # --- Financial (defaults from FinanceConfig) ---
    annual_income: float = _FC.annual_income
    down_payment_available: float = _FC.down_payment_available
    down_payment_target: float = 210_000
    liquid_reserve: float = 30_000
    monthly_debt_obligations: float = _FC.monthly_debt_obligations
    real_monthly_expenses: float = 600
    contract_rate: float = _FC.contract_rate
    amortization_years: int = _FC.amortization_years
    gds_limit: float = _FC.gds_limit
    tds_limit: float = _FC.tds_limit
    first_time_buyer: bool = _FC.first_time_buyer

    # --- Utility estimates (defaults from FinanceConfig) ---
    lender_heat_estimate: float = _FC.lender_heat_estimate
    hydro_estimate: float = _FC.hydro_estimate
    insurance_estimate: float = _FC.insurance_estimate

    # --- Budget (general) ---
    price_min: float = 0  # kept for search URL compat; not shown in UI
    price_target: float = 450_000
    price_ceiling: float = 450_000
    monthly_allin_ceiling: float = 2_100
    monthly_allin_target: float = 0  # 0 = not set; goal to optimize DP toward

    # --- Budget (condo / apartment) ---
    max_maintenance_fee: float = 0  # 0 = no limit
    condo_max_price_per_sqft: float = 0

    # --- Budget (freehold) ---
    max_taxes_annual: float = 0
    freehold_max_price_per_sqft: float = 0

    # --- Location ---
    cities: list[str] = field(default_factory=list)
    neighbourhoods: list[str] = field(default_factory=list)
    proximity_to: str = ""  # legacy single string, migrated to proximity_pois
    proximity_pois: list[str] = field(default_factory=list)
    province: str = "Ontario"
    home_address: str = ""  # where the buyer lives now
    home_lat: float = 0.0
    home_lon: float = 0.0
    work_address: str = ""
    work_lat: float = 0.0
    work_lon: float = 0.0
    city_coords: dict[str, list[float]] = field(default_factory=dict)  # city -> [lat, lon]

    # --- Property requirements ---
    min_beds: int = 2
    preferred_beds: int = 3
    min_baths: int = 1
    min_sqft: int = 900
    preferred_sqft: int = 1200
    parking_required: bool = True
    outdoor_space_required: bool = True
    laundry_insuite: bool = True
    pets_description: str = ""
    property_types: list[str] = field(default_factory=lambda: ["condo", "townhouse"])

    # --- Fee limits ---
    fee_per_sqft_warn: float = 0.80
    fee_per_sqft_reject: float = 1.00

    # --- Buffer / watch settings ---
    price_buffer_pct: float = 10.0
    monthly_buffer_pct: float = 10.0
    fee_buffer_pct: float = 15.0
    show_buffer_listings: bool = True

    # --- Search site selection ---
    search_sites: list[str] = field(default_factory=list)  # empty = all sites

    # --- Deal breakers (free-form) ---
    deal_breakers: list[str] = field(default_factory=list)

    # --- Due diligence flags ---
    due_diligence: list[str] = field(default_factory=list)

    # --- Timeline (dates as YYYY-MM-DD strings, empty = not set) ---
    close_by_date: str = ""
    earliest_offer_date: str = ""
    latest_offer_date: str = ""
    deadline: str = ""  # legacy free-text
    offer_window: str = ""  # legacy free-text
    risk_notes: str = ""

    def to_finance_dict(self) -> dict:
        """Convert profile financial fields to the config.yaml-compatible dict."""
        return {
            "down_payment_available": self.down_payment_available,
            "down_payment_min": self.down_payment_target,
            "annual_income": self.annual_income,
            "monthly_debt_obligations": self.monthly_debt_obligations,
            "contract_rate": self.contract_rate,
            "amortization_years": self.amortization_years,
            "gds_limit": self.gds_limit,
            "tds_limit": self.tds_limit,
            "lender_heat_estimate": self.lender_heat_estimate,
            "hydro_estimate": self.hydro_estimate,
            "insurance_estimate": self.insurance_estimate,
            "first_time_buyer": self.first_time_buyer,
        }

    def buffered_price_ceiling(self) -> float:
        return self.price_ceiling * (1 + self.price_buffer_pct / 100)

    def buffered_monthly_ceiling(self) -> float:
        return self.monthly_allin_ceiling * (1 + self.monthly_buffer_pct / 100)

    def buffered_fee_reject(self) -> float:
        return self.fee_per_sqft_reject * (1 + self.fee_buffer_pct / 100)

    def search_cities(self) -> list[str]:
        return self.cities if self.cities else ["oshawa", "pickering", "ajax", "whitby", "clarington"]

    def home_point(self) -> Optional[tuple[float, float]]:
        if self.home_lat or self.home_lon:
            return (self.home_lat, self.home_lon)
        return None

    def work_point(self) -> Optional[tuple[float, float]]:
        if self.work_lat or self.work_lon:
            return (self.work_lat, self.work_lon)
        return None


def _profile_path(name: str) -> Path:
    safe = re.sub(r'[^a-z0-9]+', '_', name.lower()).strip('_')
    return PROFILES_DIR / f"{safe}.yaml"


def profile_exists(name: str) -> bool:
    return _profile_path(name).exists()


def save_profile(profile: BuyerProfile) -> Path:
    PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    path = _profile_path(profile.name)
    data = asdict(profile)
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
    logger.info("Saved profile: %s -> %s", profile.name, path)
    return path


def load_profile(name: str) -> Optional[BuyerProfile]:
    path = _profile_path(name)
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return _dict_to_profile(data)
    except Exception as e:
        logger.error("Failed to load profile %s: %s", name, e)
        return None


def list_profiles() -> list[str]:
    PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    names = []
    for p in sorted(PROFILES_DIR.glob("*.yaml")):
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            names.append(data.get("name", p.stem))
        except Exception:
            names.append(p.stem)
    return names


def delete_profile(name: str) -> bool:
    path = _profile_path(name)
    if path.exists():
        path.unlink()
        logger.info("Deleted profile: %s", name)
        return True
    return False


def get_active_profile_name() -> str:
    """Read which profile is currently active from config.yaml."""
    try:
        if CONFIG_PATH.exists():
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            return data.get("active_profile", "")
    except Exception:
        pass
    return ""


def set_active_profile_name(name: str) -> None:
    """Write the active profile name into config.yaml."""
    try:
        data = {}
        if CONFIG_PATH.exists():
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        data["active_profile"] = name
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False)
    except Exception as e:
        logger.error("Failed to set active profile: %s", e)


def _dict_to_profile(data: dict) -> BuyerProfile:
    p = BuyerProfile()
    for k, v in data.items():
        if hasattr(p, k):
            expected = type(getattr(p, k))
            if expected == list and not isinstance(v, list):
                v = [v] if v else []
            try:
                setattr(p, k, v)
            except (TypeError, ValueError):
                pass
    # Migrate legacy proximity_to string → proximity_pois list
    if p.proximity_to and not p.proximity_pois:
        p.proximity_pois = [s.strip() for s in re.split(r'[/,;]', p.proximity_to) if s.strip()]
    return p
