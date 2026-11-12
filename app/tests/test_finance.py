"""Full test coverage for mortgage/qualification math."""

import pytest
from listingtracker.finance import (
    FinanceConfig,
    monthly_payment,
    semi_annual_to_monthly,
    down_payment_required,
    cmhc_insurance_premium,
    ontario_land_transfer_tax,
    first_time_buyer_rebate,
    estimated_closing_costs,
    monthly_carrying_cost,
    max_qualifying_price,
    qualifies,
    cash_left_after_close,
    price_per_sqft,
    fee_per_sqft,
    down_payment_for_budget,
    price_target_for_budget,
)


class TestSemiAnnualToMonthly:
    def test_zero_rate(self):
        assert semi_annual_to_monthly(0.0) == 0.0

    def test_typical_rate(self):
        monthly = semi_annual_to_monthly(0.04)
        assert pytest.approx(monthly, rel=1e-6) == ((1.02) ** (1 / 6) - 1)


class TestMonthlyPayment:
    def test_zero_principal(self):
        assert monthly_payment(0, 0.04, 25) == 0.0

    def test_zero_rate(self):
        result = monthly_payment(300_000, 0.0, 25)
        assert pytest.approx(result, rel=1e-6) == 300_000 / 300

    def test_typical_mortgage(self):
        pmt = monthly_payment(300_000, 0.04, 25)
        assert 1_570 < pmt < 1_590

    def test_higher_rate(self):
        pmt = monthly_payment(300_000, 0.06, 25)
        assert 1_900 < pmt < 1_950


class TestDownPaymentRequired:
    def test_zero(self):
        assert down_payment_required(0) == 0.0

    def test_under_500k(self):
        assert down_payment_required(400_000) == 20_000

    def test_at_500k(self):
        assert down_payment_required(500_000) == 25_000

    def test_between_500k_and_1m(self):
        dp = down_payment_required(700_000)
        assert dp == 500_000 * 0.05 + 200_000 * 0.10

    def test_at_1m(self):
        dp = down_payment_required(1_000_000)
        assert dp == 200_000

    def test_over_1m(self):
        dp = down_payment_required(1_500_000)
        assert dp == 300_000


class TestCMHCInsurance:
    def test_no_insurance_20pct_down(self):
        premium = cmhc_insurance_premium(500_000, 100_000)
        assert premium == 0.0

    def test_no_insurance_25pct_down(self):
        premium = cmhc_insurance_premium(400_000, 100_000)
        assert premium == 0.0

    def test_5pct_down(self):
        premium = cmhc_insurance_premium(400_000, 20_000)
        mortgage = 380_000
        assert premium == mortgage * 0.04

    def test_10pct_down(self):
        premium = cmhc_insurance_premium(400_000, 40_000)
        mortgage = 360_000
        assert premium == mortgage * 0.031

    def test_15pct_down(self):
        premium = cmhc_insurance_premium(400_000, 60_000)
        mortgage = 340_000
        assert premium == mortgage * 0.028

    def test_zero_price(self):
        assert cmhc_insurance_premium(0, 0) == 0.0

    def test_down_exceeds_price(self):
        assert cmhc_insurance_premium(300_000, 400_000) == 0.0


class TestOntarioLTT:
    def test_zero(self):
        assert ontario_land_transfer_tax(0) == 0.0

    def test_under_55k(self):
        assert ontario_land_transfer_tax(50_000) == 50_000 * 0.005

    def test_at_250k(self):
        tax = ontario_land_transfer_tax(250_000)
        expected = 55_000 * 0.005 + (250_000 - 55_000) * 0.01
        assert pytest.approx(tax) == expected

    def test_at_400k(self):
        tax = ontario_land_transfer_tax(400_000)
        expected = (
            55_000 * 0.005
            + (250_000 - 55_000) * 0.01
            + (400_000 - 250_000) * 0.015
        )
        assert pytest.approx(tax) == expected

    def test_at_500k(self):
        tax = ontario_land_transfer_tax(500_000)
        expected = (
            55_000 * 0.005
            + (250_000 - 55_000) * 0.01
            + (400_000 - 250_000) * 0.015
            + (500_000 - 400_000) * 0.02
        )
        assert pytest.approx(tax) == expected

    def test_at_2m(self):
        tax = ontario_land_transfer_tax(2_000_000)
        expected = (
            55_000 * 0.005
            + (250_000 - 55_000) * 0.01
            + (400_000 - 250_000) * 0.015
            + (2_000_000 - 400_000) * 0.02
        )
        assert pytest.approx(tax) == expected

    def test_over_2m(self):
        tax = ontario_land_transfer_tax(3_000_000)
        expected = (
            55_000 * 0.005
            + (250_000 - 55_000) * 0.01
            + (400_000 - 250_000) * 0.015
            + (2_000_000 - 400_000) * 0.02
            + (3_000_000 - 2_000_000) * 0.025
        )
        assert pytest.approx(tax) == expected


class TestFirstTimeBuyerRebate:
    def test_rebate_under_cap(self):
        assert first_time_buyer_rebate(3_000) == 3_000

    def test_rebate_at_cap(self):
        assert first_time_buyer_rebate(4_000) == 4_000

    def test_rebate_over_cap(self):
        assert first_time_buyer_rebate(6_000) == 4_000


class TestEstimatedClosingCosts:
    def test_first_time_buyer_gets_rebate(self):
        cost_ft = estimated_closing_costs(350_000, 240_000, first_time=True)
        cost_no = estimated_closing_costs(350_000, 240_000, first_time=False)
        assert cost_ft < cost_no

    def test_includes_legal_fees(self):
        cost = estimated_closing_costs(350_000, 240_000)
        assert cost >= 2_500  # at least the legal/misc baseline


class TestMonthlyCarryingCost:
    def test_basic_scenario(self):
        cfg = FinanceConfig(
            down_payment_available=240_000,
            contract_rate=0.04,
            insurance_estimate=40,
            lender_heat_estimate=100,
            hydro_estimate=90,
        )
        cost = monthly_carrying_cost(
            purchase_price=400_000,
            maintenance_fee=500,
            taxes_annual=3_000,
            fee_includes="",
            cfg=cfg,
        )
        assert cost > 0
        # P&I + 500 fee + 250 tax + 40 ins + 100 heat + 90 hydro
        assert cost > 500 + 250 + 40 + 100 + 90

    def test_fee_includes_heat_and_hydro(self):
        cfg = FinanceConfig()
        cost_without = monthly_carrying_cost(400_000, 500, 3_000, "", cfg)
        cost_with = monthly_carrying_cost(400_000, 500, 3_000, "heat, hydro", cfg)
        assert cost_with < cost_without
        assert pytest.approx(cost_without - cost_with) == cfg.lender_heat_estimate + cfg.hydro_estimate


class TestMaxQualifyingPrice:
    def test_returns_plausible_value(self):
        cfg = FinanceConfig()
        max_price = max_qualifying_price(500, 3_000, "", cfg)
        assert 250_000 < max_price < 600_000

    def test_higher_income_qualifies_more(self):
        cfg_low = FinanceConfig(annual_income=60_000)
        cfg_high = FinanceConfig(annual_income=120_000)
        p_low = max_qualifying_price(500, 3_000, "", cfg_low)
        p_high = max_qualifying_price(500, 3_000, "", cfg_high)
        assert p_high > p_low

    def test_debt_reduces_qualifying_price(self):
        cfg_no_debt = FinanceConfig(monthly_debt_obligations=0)
        cfg_debt = FinanceConfig(monthly_debt_obligations=500)
        p_no = max_qualifying_price(500, 3_000, "", cfg_no_debt)
        p_debt = max_qualifying_price(500, 3_000, "", cfg_debt)
        assert p_no > p_debt


class TestQualifies:
    def test_affordable_unit(self):
        cfg = FinanceConfig(down_payment_available=240_000, annual_income=60_000)
        assert qualifies(350_000, 400, 2_500, "heat", cfg) is True

    def test_too_expensive(self):
        cfg = FinanceConfig(down_payment_available=50_000, annual_income=40_000)
        assert qualifies(800_000, 900, 5_000, "", cfg) is False


class TestCashLeftAfterClose:
    def test_affordable_purchase(self):
        cfg = FinanceConfig(down_payment_available=240_000)
        cash = cash_left_after_close(200_000, cfg)
        assert cash > 0

    def test_expensive_purchase_negative(self):
        cfg = FinanceConfig(down_payment_available=50_000)
        cash = cash_left_after_close(800_000, cfg)
        assert cash < 0


class TestPricePerSqft:
    def test_range(self):
        result = price_per_sqft(500_000, 600, 800)
        assert pytest.approx(result) == 500_000 / 700

    def test_min_only(self):
        result = price_per_sqft(500_000, 700, None)
        assert pytest.approx(result) == 500_000 / 700

    def test_max_only(self):
        result = price_per_sqft(500_000, None, 700)
        assert pytest.approx(result) == 500_000 / 700

    def test_none(self):
        assert price_per_sqft(500_000, None, None) is None

    def test_zero_price(self):
        assert price_per_sqft(0, 600, 800) is None


class TestFeePerSqft:
    def test_basic(self):
        result = fee_per_sqft(500, 600, 800)
        assert pytest.approx(result) == 500 / 700

    def test_none(self):
        assert fee_per_sqft(500, None, None) is None

    def test_zero_fee(self):
        assert fee_per_sqft(0, 600, 800) is None


class TestFinanceConfig:
    def test_default_stress_rate(self):
        cfg = FinanceConfig(contract_rate=0.04)
        assert cfg.effective_stress_rate == 0.06

    def test_stress_rate_floor(self):
        cfg = FinanceConfig(contract_rate=0.02)
        assert cfg.effective_stress_rate == 0.0525

    def test_custom_stress_rate(self):
        cfg = FinanceConfig(stress_rate=0.07)
        assert cfg.effective_stress_rate == 0.07


class TestDownPaymentForBudget:
    def test_finds_dp_for_target(self):
        cfg = FinanceConfig(down_payment_available=240_000)
        dp = down_payment_for_budget(450_000, 500, 3000, "", 2100, cfg)
        assert dp is not None
        assert dp > 0
        # Verify: monthly cost at this DP should be <= target
        from listingtracker.finance import _calc_dp_scenario
        s = _calc_dp_scenario(450_000, dp, 500, 3000, "", cfg)
        assert s["monthly_total"] <= 2100 + 1  # allow $1 rounding

    def test_already_under_budget(self):
        cfg = FinanceConfig(down_payment_available=400_000)
        dp = down_payment_for_budget(450_000, 200, 2000, "", 3000, cfg)
        assert dp is not None
        # Should return CMHC minimum since already under budget
        from listingtracker.finance import down_payment_required
        assert dp <= down_payment_required(450_000) + 1

    def test_impossible_returns_none(self):
        cfg = FinanceConfig(down_payment_available=50_000)
        # Very high fixed costs that exceed budget even with 100% down
        dp = down_payment_for_budget(450_000, 2500, 10000, "", 100, cfg)
        assert dp is None

    def test_zero_price_returns_none(self):
        cfg = FinanceConfig()
        assert down_payment_for_budget(0, 500, 3000, "", 2000, cfg) is None

    def test_higher_budget_needs_less_dp(self):
        cfg = FinanceConfig(down_payment_available=240_000)
        dp_tight = down_payment_for_budget(450_000, 500, 3000, "", 1800, cfg)
        dp_loose = down_payment_for_budget(450_000, 500, 3000, "", 2500, cfg)
        assert dp_tight is not None and dp_loose is not None
        assert dp_tight > dp_loose


class TestPriceTargetForBudget:
    def test_finds_price_for_target(self):
        cfg = FinanceConfig(down_payment_available=240_000)
        price = price_target_for_budget(500, 3000, "", 2100, cfg)
        assert price > 0
        # Verify: monthly cost at this price should be <= target
        cost = monthly_carrying_cost(price, 500, 3000, "", cfg)
        assert cost <= 2100 + 1  # allow $1 rounding

    def test_higher_budget_higher_price(self):
        cfg = FinanceConfig(down_payment_available=240_000)
        p_tight = price_target_for_budget(500, 3000, "", 1800, cfg)
        p_loose = price_target_for_budget(500, 3000, "", 2500, cfg)
        assert p_loose > p_tight

    def test_zero_budget(self):
        cfg = FinanceConfig()
        assert price_target_for_budget(500, 3000, "", 0, cfg) == 0.0

    def test_high_fees_reduce_price_target(self):
        cfg = FinanceConfig(down_payment_available=240_000)
        p_low_fee = price_target_for_budget(300, 2000, "", 2100, cfg)
        p_high_fee = price_target_for_budget(800, 5000, "", 2100, cfg)
        assert p_low_fee > p_high_fee
