"""Tests for change detection logic."""

import pytest
from listingtracker.models import Listing, Snapshot, Status, Change
from listingtracker.diffing import diff_snapshots, sort_changes


def _make_listing(**kwargs) -> Listing:
    defaults = dict(id=1, url="https://example.com", site="test", address="123 Main St", city="Oshawa")
    defaults.update(kwargs)
    return Listing(**defaults)


def _make_snapshot(**kwargs) -> Snapshot:
    defaults = dict(
        id=1,
        listing_id=1,
        fetched_at="2024-01-01T00:00:00",
        status=Status.FOR_SALE,
        list_price=400_000,
        maintenance_fee=500,
        taxes_annual=3_000,
        beds=2,
        baths=1,
        sqft_min=700,
        sqft_max=800,
    )
    defaults.update(kwargs)
    return Snapshot(**defaults)


class TestDiffSnapshots:
    def test_no_change(self):
        listing = _make_listing()
        old = _make_snapshot()
        new = _make_snapshot(id=2, fetched_at="2024-01-02T00:00:00")
        changes = diff_snapshots(listing, old, new)
        assert changes == []

    def test_price_change(self):
        listing = _make_listing()
        old = _make_snapshot(list_price=400_000)
        new = _make_snapshot(id=2, list_price=380_000)
        changes = diff_snapshots(listing, old, new)
        assert len(changes) == 1
        assert changes[0].field == "list_price"
        assert changes[0].severity == "warning"
        assert "down" in changes[0].description
        assert "$20,000" in changes[0].description

    def test_price_increase(self):
        listing = _make_listing()
        old = _make_snapshot(list_price=400_000)
        new = _make_snapshot(id=2, list_price=420_000)
        changes = diff_snapshots(listing, old, new)
        assert "up" in changes[0].description

    def test_status_sold_is_critical(self):
        listing = _make_listing()
        old = _make_snapshot(status=Status.FOR_SALE)
        new = _make_snapshot(id=2, status=Status.SOLD)
        changes = diff_snapshots(listing, old, new)
        assert len(changes) == 1
        assert changes[0].field == "status"
        assert changes[0].severity == "critical"

    def test_status_sold_conditional_is_critical(self):
        listing = _make_listing()
        old = _make_snapshot(status=Status.FOR_SALE)
        new = _make_snapshot(id=2, status=Status.SOLD_CONDITIONAL)
        changes = diff_snapshots(listing, old, new)
        assert changes[0].severity == "critical"

    def test_status_terminated_is_critical(self):
        listing = _make_listing()
        old = _make_snapshot(status=Status.FOR_SALE)
        new = _make_snapshot(id=2, status=Status.TERMINATED)
        changes = diff_snapshots(listing, old, new)
        assert changes[0].severity == "critical"

    def test_status_blocked_is_warning(self):
        listing = _make_listing()
        old = _make_snapshot(status=Status.FOR_SALE)
        new = _make_snapshot(id=2, status=Status.BLOCKED)
        changes = diff_snapshots(listing, old, new)
        assert changes[0].severity == "warning"

    def test_fee_change(self):
        listing = _make_listing()
        old = _make_snapshot(maintenance_fee=500)
        new = _make_snapshot(id=2, maintenance_fee=550)
        changes = diff_snapshots(listing, old, new)
        assert len(changes) == 1
        assert changes[0].field == "maintenance_fee"

    def test_multiple_changes(self):
        listing = _make_listing()
        old = _make_snapshot(list_price=400_000, status=Status.FOR_SALE)
        new = _make_snapshot(id=2, list_price=380_000, status=Status.SOLD)
        changes = diff_snapshots(listing, old, new)
        assert len(changes) == 2
        fields = {c.field for c in changes}
        assert fields == {"list_price", "status"}

    def test_first_snapshot_for_sale(self):
        listing = _make_listing()
        snap = _make_snapshot(status=Status.FOR_SALE)
        changes = diff_snapshots(listing, None, snap)
        assert changes == []

    def test_first_snapshot_not_for_sale(self):
        listing = _make_listing()
        snap = _make_snapshot(status=Status.SOLD)
        changes = diff_snapshots(listing, None, snap)
        assert len(changes) == 1
        assert changes[0].severity == "warning"

    def test_none_to_value_not_a_change(self):
        listing = _make_listing()
        old = _make_snapshot(parking=None)
        new = _make_snapshot(id=2, parking=None)
        changes = diff_snapshots(listing, old, new)
        assert changes == []

    def test_none_to_actual_value_is_change(self):
        listing = _make_listing()
        old = _make_snapshot(parking=None)
        new = _make_snapshot(id=2, parking="1 Underground")
        changes = diff_snapshots(listing, old, new)
        assert len(changes) == 1
        assert changes[0].field == "parking"


class TestSortChanges:
    def test_sorts_by_severity(self):
        listing = _make_listing()
        changes = [
            Change(listing_id=1, listing=listing, field="beds", old_value=2, new_value=3, severity="info"),
            Change(listing_id=1, listing=listing, field="status", old_value="FOR_SALE", new_value="SOLD", severity="critical"),
            Change(listing_id=1, listing=listing, field="list_price", old_value=400_000, new_value=380_000, severity="warning"),
        ]
        sorted_c = sort_changes(changes)
        assert sorted_c[0].severity == "critical"
        assert sorted_c[1].severity == "warning"
        assert sorted_c[2].severity == "info"
