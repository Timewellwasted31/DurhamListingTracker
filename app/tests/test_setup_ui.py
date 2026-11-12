"""Headless UI tests for the setup wizard and the profile editor's location handling."""

import os
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QDialog

from listingtracker import geo
from listingtracker.profiles import BuyerProfile
from listingtracker.ui.profile_dialog import ProfileDialog
from listingtracker.ui.setup_wizard import SetupWizard


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_wizard_requires_each_page_before_finishing(app, tmp_path):
    with patch("listingtracker.profiles.PROFILES_DIR", tmp_path):
        (tmp_path / "taken.yaml").write_text("name: Taken\n", encoding="utf-8")
        wizard = SetupWizard()

        assert "name" in wizard._validate(0)
        wizard.name_edit.setText("Taken")
        assert "already exists" in wizard._validate(0)
        wizard.name_edit.setText("First home")
        assert wizard._validate(0) == ""

        assert "income" in wizard._validate(1)
        wizard.income.setValue(90_000)
        assert wizard._qualify_estimate > 0
        wizard.down_payment.setValue(10_000)
        wizard.price_ceiling.setValue(400_000)
        assert wizard._monthly_estimate > 0
        wizard.monthly_ceiling.setValue(2_500)
        assert "at least $20,000" in wizard._validate(1)
        wizard.down_payment.setValue(60_000)
        assert wizard._validate(1) == ""

        assert "city" in wizard._validate(2)
        wizard.location._add_city("Whitby", (43.88, -78.94))
        wizard.location._add_city("whitby", (43.88, -78.94))  # duplicate ignored
        assert wizard._validate(2) == ""

        for cb in wizard.type_checks.values():
            cb.setChecked(False)
        assert wizard._validate(3) != ""
        wizard._finish(True)  # incomplete page: bounced back, not accepted
        assert wizard.result() != QDialog.DialogCode.Accepted
        assert wizard.stack.currentIndex() == 3
        wizard.type_checks["townhouse"].setChecked(True)

        wizard._finish(True)
        assert wizard.result() == QDialog.DialogCode.Accepted
        assert wizard.run_search

        profile = wizard.get_profile()
        assert profile.name == "First home"
        assert profile.annual_income == 90_000
        assert profile.down_payment_available == 60_000
        assert profile.price_ceiling == 400_000
        assert profile.monthly_allin_ceiling == 2_500
        assert profile.contract_rate == pytest.approx(0.04)
        assert profile.cities == ["Whitby"]
        assert profile.city_coords == {"Whitby": [43.88, -78.94]}
        assert profile.property_types == ["townhouse"]


def test_profile_dialog_keeps_fields_it_does_not_edit(app):
    original = BuyerProfile(
        name="Keeper",
        search_sites=["strata.ca", "realtor.ca"],
        price_min=150_000,
        neighbourhoods=["Downtown"],
        property_types=["condo", "house"],
        home_address="12 Main St, Oshawa, Ontario", home_lat=43.9, home_lon=-78.86,
        work_address="Toronto City Hall", work_lat=43.6535, work_lon=-79.3839,
        cities=["Oshawa", "Whitby"],
        city_coords={"Oshawa": [43.8976, -78.8635], "Whitby": [43.88, -78.94]},
    )
    with patch("listingtracker.geo.driving_route", return_value=geo.RouteInfo(60, 50)):
        saved = ProfileDialog(original).get_profile()
    assert saved == original
