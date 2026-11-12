"""Mortgage qualification and carrying-cost math — pure functions, no I/O."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class FinanceConfig:
    down_payment_available: float = 240_000
    down_payment_min: float = 0
    annual_income: float = 60_000
    monthly_debt_obligations: float = 0
    contract_rate: float = 0.04
    stress_rate: Optional[float] = None
    amortization_years: int = 25
    gds_limit: float = 0.39
    tds_limit: float = 0.44
    lender_heat_estimate: float = 100
    hydro_estimate: float = 90
    insurance_estimate: float = 40
    first_time_buyer: bool = False

    @property
    def effective_stress_rate(self) -> float:
        if self.stress_rate is not None:
            return self.stress_rate
        return max(0.0525, self.contract_rate + 0.02)


def monthly_payment(principal: float, annual_rate: float, amort_years: int) -> float:
    """Standard Canadian mortgage payment: semi-annual compounding, monthly payments."""
    if principal <= 0:
        return 0.0
    if annual_rate <= 0:
        return principal / (amort_years * 12)
    monthly_rate = semi_annual_to_monthly(annual_rate)
    n = amort_years * 12
    return principal * monthly_rate / (1 - (1 + monthly_rate) ** -n)


def semi_annual_to_monthly(annual_rate: float) -> float:
    """Convert annual rate with semi-annual compounding to effective monthly rate."""
    semi = annual_rate / 2
    return (1 + semi) ** (1 / 6) - 1


def down_payment_required(purchase_price: float) -> float:
    """CMHC minimum down payment rules."""
    if purchase_price <= 0:
        return 0.0
    if purchase_price <= 500_000:
        return purchase_price * 0.05
    elif purchase_price <= 999_999.99:
        return 500_000 * 0.05 + (purchase_price - 500_000) * 0.10
    else:
        return purchase_price * 0.20


def cmhc_insurance_premium(purchase_price: float, down: float) -> float:
    """CMHC mortgage insurance premium on the insured amount."""
    if purchase_price <= 0 or down >= purchase_price:
        return 0.0
    ltv = (purchase_price - down) / purchase_price
    if ltv <= 0.80:
        return 0.0  # conventional mortgage, no insurance required
    elif ltv <= 0.85:
        rate = 0.0280
    elif ltv <= 0.90:
        rate = 0.0310
    elif ltv <= 0.95:
        rate = 0.0400
    else:
        return 0.0  # not insurable
    mortgage = purchase_price - down
    return mortgage * rate


def ontario_land_transfer_tax(purchase_price: float) -> float:
    """Ontario provincial LTT. Durham has no municipal LTT."""
    if purchase_price <= 0:
        return 0.0
    tax = 0.0
    brackets = [
        (55_000, 0.005),
        (250_000 - 55_000, 0.01),
        (400_000 - 250_000, 0.015),
        (2_000_000 - 400_000, 0.02),
        (float("inf"), 0.025),
    ]
    remaining = purchase_price
    for width, rate in brackets:
        taxable = min(remaining, width)
        tax += taxable * rate
        remaining -= taxable
        if remaining <= 0:
            break
    return tax


def first_time_buyer_rebate(ltt: float) -> float:
    """Ontario first-time buyer LTT rebate, capped at $4,000."""
    return min(ltt, 4_000.0)


def estimated_closing_costs(purchase_price: float, down: float,
                            first_time: bool = False) -> float:
    """Estimate total closing costs: LTT + CMHC premium + legal + misc."""
    ltt = ontario_land_transfer_tax(purchase_price)
    if first_time:
        ltt = max(0, ltt - first_time_buyer_rebate(ltt))
    insurance = cmhc_insurance_premium(purchase_price, down)
    legal_misc = 2_500  # title insurance, legal fees, adjustments
    return ltt + insurance + legal_misc


def monthly_carrying_cost(
    purchase_price: float,
    maintenance_fee: float,
    taxes_annual: float,
    fee_includes: str,
    cfg: FinanceConfig,
) -> float:
    """Total monthly outlay at the contract rate."""
    dp = min(cfg.down_payment_available, purchase_price)
    mortgage = purchase_price - dp
    insurance = cmhc_insurance_premium(purchase_price, dp)
    total_mortgage = mortgage + insurance
    pi = monthly_payment(total_mortgage, cfg.contract_rate, cfg.amortization_years)

    monthly_tax = taxes_annual / 12 if taxes_annual else 0
    includes = _parse_fee_includes(fee_includes)

    heat = 0.0 if "heat" in includes else cfg.lender_heat_estimate
    hydro = 0.0 if "hydro" in includes else cfg.hydro_estimate

    return pi + maintenance_fee + monthly_tax + cfg.insurance_estimate + heat + hydro


def max_qualifying_price(
    maintenance_fee: float,
    taxes_annual: float,
    fee_includes: str,
    cfg: FinanceConfig,
) -> float:
    """Binary search for the highest purchase price that passes GDS and TDS."""
    includes = _parse_fee_includes(fee_includes)
    lo, hi = 0.0, 2_000_000.0
    for _ in range(100):
        mid = (lo + hi) / 2
        if _qualifies_at_price(mid, maintenance_fee, taxes_annual, fee_includes, cfg, _includes=includes):
            lo = mid
        else:
            hi = mid
    return lo


def qualifies(
    purchase_price: float,
    maintenance_fee: float,
    taxes_annual: float,
    fee_includes: str,
    cfg: FinanceConfig,
) -> bool:
    return _qualifies_at_price(
        purchase_price, maintenance_fee, taxes_annual, fee_includes, cfg
    )


def _qualifies_at_price(
    purchase_price: float,
    maintenance_fee: float,
    taxes_annual: float,
    fee_includes: str,
    cfg: FinanceConfig,
    _includes: set[str] | None = None,
) -> bool:
    """Check GDS and TDS at the stress rate."""
    dp = min(cfg.down_payment_available, purchase_price)
    mortgage = purchase_price - dp
    insurance = cmhc_insurance_premium(purchase_price, dp)
    total_mortgage = mortgage + insurance

    stress = cfg.effective_stress_rate
    pi_stress = monthly_payment(total_mortgage, stress, cfg.amortization_years)

    monthly_income = cfg.annual_income / 12
    if monthly_income <= 0:
        return False

    monthly_tax = taxes_annual / 12 if taxes_annual else 0
    includes = _includes if _includes is not None else _parse_fee_includes(fee_includes)

    heat = 0.0 if "heat" in includes else cfg.lender_heat_estimate
    half_fee = maintenance_fee * 0.5 if maintenance_fee else 0

    gds_numerator = pi_stress + monthly_tax + heat + half_fee
    gds = gds_numerator / monthly_income

    tds = (gds_numerator + cfg.monthly_debt_obligations) / monthly_income

    return gds <= cfg.gds_limit and tds <= cfg.tds_limit


def cash_left_after_close(purchase_price: float, cfg: FinanceConfig) -> float:
    actual_dp = min(cfg.down_payment_available, purchase_price)
    closing = estimated_closing_costs(purchase_price, actual_dp, cfg.first_time_buyer)
    return cfg.down_payment_available - actual_dp - closing


def _per_sqft(value: float, sqft_min: Optional[int],
              sqft_max: Optional[int]) -> Optional[float]:
    if value is None or value <= 0:
        return None
    if sqft_min and sqft_max:
        midpoint = (sqft_min + sqft_max) / 2
    elif sqft_min:
        midpoint = sqft_min
    elif sqft_max:
        midpoint = sqft_max
    else:
        return None
    if midpoint <= 0:
        return None
    return value / midpoint


def price_per_sqft(price: float, sqft_min: Optional[int],
                   sqft_max: Optional[int]) -> Optional[float]:
    return _per_sqft(price, sqft_min, sqft_max)


def fee_per_sqft(fee: float, sqft_min: Optional[int],
                 sqft_max: Optional[int]) -> Optional[float]:
    return _per_sqft(fee, sqft_min, sqft_max)


def _calc_dp_scenario(
    purchase_price: float, dp_amount: float,
    maintenance_fee: float, taxes_annual: float,
    fee_includes: str, cfg: FinanceConfig,
) -> dict:
    """Compute mortgage numbers for a single down-payment amount."""
    dp = min(dp_amount, purchase_price)
    dp = max(dp, down_payment_required(purchase_price))
    dp_pct = (dp / purchase_price * 100) if purchase_price > 0 else 0
    mortgage_principal = purchase_price - dp
    insurance = cmhc_insurance_premium(purchase_price, dp)
    total_mortgage = mortgage_principal + insurance
    monthly_pi = monthly_payment(total_mortgage, cfg.contract_rate, cfg.amortization_years)
    monthly_tax = taxes_annual / 12 if taxes_annual else 0
    includes = _parse_fee_includes(fee_includes)
    heat = 0.0 if "heat" in includes else cfg.lender_heat_estimate
    hydro = 0.0 if "hydro" in includes else cfg.hydro_estimate
    total_monthly = monthly_pi + maintenance_fee + monthly_tax + cfg.insurance_estimate + heat + hydro
    total_interest = monthly_pi * cfg.amortization_years * 12 - total_mortgage
    return {
        "down_payment": dp,
        "down_payment_pct": dp_pct,
        "mortgage_principal": mortgage_principal,
        "cmhc_insurance": insurance,
        "total_mortgage": total_mortgage,
        "monthly_pi": monthly_pi,
        "monthly_tax": monthly_tax,
        "monthly_maintenance": maintenance_fee,
        "monthly_insurance": cfg.insurance_estimate,
        "monthly_heat": heat,
        "monthly_hydro": hydro,
        "monthly_total": total_monthly,
        "total_interest": total_interest,
    }


def mortgage_breakdown(
    purchase_price: float,
    maintenance_fee: float,
    taxes_annual: float,
    fee_includes: str,
    cfg: FinanceConfig,
) -> dict:
    """Compute mortgage breakdown with min/max DP scenarios + delta."""
    dp_min_amt = cfg.down_payment_min if cfg.down_payment_min > 0 else down_payment_required(purchase_price)
    dp_max_amt = cfg.down_payment_available

    hi = _calc_dp_scenario(purchase_price, dp_max_amt, maintenance_fee, taxes_annual, fee_includes, cfg)
    lo = _calc_dp_scenario(purchase_price, dp_min_amt, maintenance_fee, taxes_annual, fee_includes, cfg)

    return {
        "purchase_price": purchase_price,
        "contract_rate": cfg.contract_rate,
        "amortization_years": cfg.amortization_years,
        "lo": lo,
        "hi": hi,
    }


def down_payment_for_budget(
    purchase_price: float,
    maintenance_fee: float,
    taxes_annual: float,
    fee_includes: str,
    target_monthly: float,
    cfg: FinanceConfig,
) -> Optional[float]:
    """Find the minimum down payment that brings monthly cost <= target_monthly.

    Returns None if even paying cash exceeds the target (fixed costs too high),
    or if the CMHC minimum DP already meets the target.
    """
    if purchase_price <= 0 or target_monthly <= 0:
        return None

    min_dp = down_payment_required(purchase_price)

    # Check if max DP already meets budget
    scenario_max = _calc_dp_scenario(
        purchase_price, purchase_price, maintenance_fee,
        taxes_annual, fee_includes, cfg,
    )
    if scenario_max["monthly_total"] > target_monthly:
        return None  # fixed costs alone exceed budget

    # Check if min DP already meets budget
    scenario_min = _calc_dp_scenario(
        purchase_price, min_dp, maintenance_fee,
        taxes_annual, fee_includes, cfg,
    )
    if scenario_min["monthly_total"] <= target_monthly:
        return min_dp

    # Binary search between min_dp and purchase_price
    lo, hi = min_dp, purchase_price
    for _ in range(100):
        mid = (lo + hi) / 2
        s = _calc_dp_scenario(
            purchase_price, mid, maintenance_fee,
            taxes_annual, fee_includes, cfg,
        )
        if s["monthly_total"] <= target_monthly:
            hi = mid
        else:
            lo = mid
    return hi


def price_target_for_budget(
    maintenance_fee: float,
    taxes_annual: float,
    fee_includes: str,
    target_monthly: float,
    cfg: FinanceConfig,
) -> float:
    """Find the max purchase price where monthly carrying cost <= target_monthly."""
    if target_monthly <= 0:
        return 0.0
    lo, hi = 0.0, 2_000_000.0
    for _ in range(100):
        mid = (lo + hi) / 2
        cost = monthly_carrying_cost(mid, maintenance_fee, taxes_annual, fee_includes, cfg)
        if cost <= target_monthly:
            lo = mid
        else:
            hi = mid
    return lo


def _parse_fee_includes(fee_includes: str) -> set[str]:
    if not fee_includes:
        return set()
    return {item.strip().lower() for item in fee_includes.split(",")}
