"""Configuration management via config.yaml."""

from __future__ import annotations

import logging
from dataclasses import fields as dataclass_fields
from typing import Any

import yaml

from listingtracker.paths import CONFIG_PATH
from listingtracker.finance import FinanceConfig

logger = logging.getLogger(__name__)

_FC = FinanceConfig()

DEFAULTS = {
    "down_payment_available": _FC.down_payment_available,
    "annual_income": _FC.annual_income,
    "monthly_debt_obligations": _FC.monthly_debt_obligations,
    "contract_rate": _FC.contract_rate,
    "amortization_years": _FC.amortization_years,
    "gds_limit": _FC.gds_limit,
    "tds_limit": _FC.tds_limit,
    "lender_heat_estimate": _FC.lender_heat_estimate,
    "hydro_estimate": _FC.hydro_estimate,
    "insurance_estimate": _FC.insurance_estimate,
    "first_time_buyer": _FC.first_time_buyer,
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
    fc_fields = {f.name for f in dataclass_fields(FinanceConfig)}
    return FinanceConfig(**{k: v for k, v in cfg.items() if k in fc_fields})
