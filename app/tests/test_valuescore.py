"""Tests for the value score module."""

from listingtracker.finance import FinanceConfig
from listingtracker.valuescore import compute_value_metrics, _linear_score, _inverse_score


def _cfg() -> FinanceConfig:
    return FinanceConfig(
        down_payment_available=80_000,
        annual_income=90_000,
        contract_rate=0.055,
        amortization_years=25,
        monthly_debt_obligations=0,
    )


def test_linear_score_boundaries():
    assert _linear_score(350, 350, 700) == 100.0
    assert _linear_score(700, 350, 700) == 0.0
    assert _linear_score(525, 350, 700) == 50.0


def test_linear_score_clamped():
    assert _linear_score(100, 350, 700) == 100.0
    assert _linear_score(900, 350, 700) == 0.0


def test_inverse_score_boundaries():
    assert _inverse_score(400, 400, 1200) == 0.0
    assert _inverse_score(1200, 400, 1200) == 100.0
    assert _inverse_score(800, 400, 1200) == 50.0


def test_compute_all_data():
    vm = compute_value_metrics(
        list_price=400_000, maintenance_fee=500, taxes_annual=3000,
        fee_includes="Heat, Water", sqft_min=600, sqft_max=700,
        beds=2, cfg=_cfg(),
    )
    assert vm.price_psf is not None
    assert vm.fee_psf is not None
    assert vm.tax_psf is not None
    assert vm.monthly_cost is not None
    assert vm.amenity_cost is not None
    assert vm.score is not None
    assert 0 <= vm.score <= 100


def test_compute_missing_sqft():
    vm = compute_value_metrics(
        list_price=400_000, maintenance_fee=500, taxes_annual=3000,
        fee_includes="", sqft_min=None, sqft_max=None,
        beds=2, cfg=_cfg(),
    )
    assert vm.price_psf is None
    assert vm.fee_psf is None
    assert vm.tax_psf is None
    assert vm.score is not None
    assert vm.score < 50  # penalized for missing data


def test_compute_no_maintenance_fee():
    vm = compute_value_metrics(
        list_price=500_000, maintenance_fee=None, taxes_annual=4000,
        fee_includes="", sqft_min=800, sqft_max=900,
        beds=3, cfg=_cfg(),
    )
    assert vm.fee_psf is None
    assert vm.fee_psf_score == 70.0  # freehold bonus
    assert vm.amenity_cost is None
    assert vm.score is not None


def test_score_better_value_is_higher():
    cfg = _cfg()
    cheap = compute_value_metrics(
        list_price=300_000, maintenance_fee=350, taxes_annual=2000,
        fee_includes="Heat, Water, Gas", sqft_min=700, sqft_max=800,
        beds=2, cfg=cfg,
    )
    expensive = compute_value_metrics(
        list_price=600_000, maintenance_fee=900, taxes_annual=5000,
        fee_includes="", sqft_min=500, sqft_max=550,
        beds=1, cfg=cfg,
    )
    assert cheap.score > expensive.score
