"""Configuration management via config.yaml."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from listingtracker.paths import CONFIG_PATH
from listingtracker.finance import FinanceConfig

logger = logging.getLogger(__name__)

DEFAULTS = {
    "down_payment_available": 240_000,
    "annual_income": 60_000,
    "monthly_debt_obligations": 0,
    "contract_rate": 0.04,
    "amortization_years": 25,
    "gds_limit": 0.39,
    "tds_limit": 0.44,
    "lender_heat_estimate": 100,
    "hydro_estimate": 90,
    "insurance_estimate": 40,
    "first_time_buyer": False,
    "show_browser": False,
}


def load_config() -> dict[str, Any]:
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            merged = {**DEFAULTS, **data}
            return merged
        except Exception as e:
            logger.error("Failed to load config: %s", e)
    return dict(DEFAULTS)


def save_config(data: dict[str, Any]) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)


def config_to_finance(cfg: dict[str, Any]) -> FinanceConfig:
    return FinanceConfig(
        down_payment_available=cfg.get("down_payment_available", 240_000),
        down_payment_min=cfg.get("down_payment_min", 0),
        annual_income=cfg.get("annual_income", 60_000),
        monthly_debt_obligations=cfg.get("monthly_debt_obligations", 0),
        contract_rate=cfg.get("contract_rate", 0.04),
        amortization_years=cfg.get("amortization_years", 25),
        gds_limit=cfg.get("gds_limit", 0.39),
        tds_limit=cfg.get("tds_limit", 0.44),
        lender_heat_estimate=cfg.get("lender_heat_estimate", 100),
        hydro_estimate=cfg.get("hydro_estimate", 90),
        insurance_estimate=cfg.get("insurance_estimate", 40),
        first_time_buyer=cfg.get("first_time_buyer", False),
    )
