"""Change detection between snapshots."""

from __future__ import annotations

from listingtracker.models import Listing, Snapshot, Change, Status


TRACKED_FIELDS = {
    "status": "Status",
    "list_price": "Price",
    "maintenance_fee": "Maintenance fee",
    "taxes_annual": "Annual taxes",
    "beds": "Bedrooms",
    "baths": "Bathrooms",
    "sqft_min": "Sqft (min)",
    "sqft_max": "Sqft (max)",
    "parking": "Parking",
    "locker": "Locker",
}


def diff_snapshots(
    listing: Listing,
    old: Snapshot | None,
    new: Snapshot,
) -> list[Change]:
    """Compare two snapshots and return a list of changes."""
    if old is None:
        return _first_snapshot_changes(listing, new)

    changes: list[Change] = []
    for field, label in TRACKED_FIELDS.items():
        old_val = getattr(old, field)
        new_val = getattr(new, field)

        if field == "status":
            old_val = old_val.value if isinstance(old_val, Status) else old_val
            new_val = new_val.value if isinstance(new_val, Status) else new_val

        if old_val != new_val and not (_both_none_or_empty(old_val, new_val)):
            severity = _classify_severity(field, old_val, new_val)
            desc = _describe_change(label, field, old_val, new_val)
            changes.append(
                Change(
                    listing_id=listing.id,
                    listing=listing,
                    field=field,
                    old_value=old_val,
                    new_value=new_val,
                    severity=severity,
                    description=desc,
                )
            )

    return changes


def _first_snapshot_changes(listing: Listing, snap: Snapshot) -> list[Change]:
    """For the first snapshot, report status as a change if it is not FOR_SALE."""
    changes = []
    if snap.status and snap.status != Status.FOR_SALE:
        changes.append(
            Change(
                listing_id=listing.id,
                listing=listing,
                field="status",
                old_value=None,
                new_value=snap.status.value if isinstance(snap.status, Status) else snap.status,
                severity="warning",
                description=f"Initial fetch: status is {snap.status.value}",
            )
        )
    return changes


def _both_none_or_empty(a: object, b: object) -> bool:
    return (a is None or a == "") and (b is None or b == "")


def _classify_severity(field: str, old_val: object, new_val: object) -> str:
    if field == "status":
        return _status_change_severity(old_val, new_val)
    if field == "list_price":
        return "warning"
    if field == "maintenance_fee":
        return "warning"
    return "info"


def _status_change_severity(old_status: object, new_status: object) -> str:
    critical_exits = {
        Status.SOLD.value,
        Status.SOLD_CONDITIONAL.value,
        Status.TERMINATED.value,
        Status.EXPIRED.value,
        Status.DELISTED.value,
    }
    old_str = old_status if isinstance(old_status, str) else str(old_status)
    new_str = new_status if isinstance(new_status, str) else str(new_status)

    if old_str == Status.FOR_SALE.value and new_str in critical_exits:
        return "critical"
    if new_str == Status.BLOCKED.value:
        return "warning"
    if new_str != old_str:
        return "warning"
    return "info"


def _describe_change(
    label: str, field: str, old_val: object, new_val: object
) -> str:
    if field == "list_price" and old_val is not None and new_val is not None:
        try:
            diff = float(new_val) - float(old_val)
            pct = diff / float(old_val) * 100 if float(old_val) != 0 else 0
            direction = "up" if diff > 0 else "down"
            return (
                f"{label}: ${old_val:,.0f} → ${new_val:,.0f} "
                f"({direction} ${abs(diff):,.0f}, {abs(pct):.1f}%)"
            )
        except (TypeError, ValueError):
            pass

    return f"{label}: {old_val} → {new_val}"


def sort_changes(changes: list[Change]) -> list[Change]:
    """Sort changes most-significant first: critical > warning > info."""
    priority = {"critical": 0, "warning": 1, "info": 2}
    return sorted(changes, key=lambda c: priority.get(c.severity, 3))
