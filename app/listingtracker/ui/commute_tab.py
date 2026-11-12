"""Commute tab — how far a listing is from where the buyer works and lives now."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Signal, QTimer
from PySide6.QtWidgets import QWidget, QVBoxLayout, QGridLayout, QLabel

from listingtracker import geo
from listingtracker.models import Listing
from listingtracker.profiles import BuyerProfile
from listingtracker.ui.location_editor import HOME_COLOR, WORK_COLOR
from listingtracker.ui.map_widget import MapWidget, MapMarker, MapRoute, GeoRunner

LISTING_COLOR = "#ab47bc"
KEY_STYLE = "color: #888; font-size: 12px;"
VALUE_STYLE = "color: #e0e0e0; font-size: 13px; font-weight: bold;"
NOTE_STYLE = "color: #888; font-size: 11px;"
DELTA_COLORS = {"better": "#66bb6a", "worse": "#ef5350", "same": "#888"}


class CommuteTab(QWidget):
    summary_ready = Signal(str)  # one-line summary for the listing header

    def __init__(self, listing: Listing, profile: Optional[BuyerProfile], parent=None):
        super().__init__(parent)
        self._listing = listing
        self._home = profile.home_point() if profile else None
        self._work = profile.work_point() if profile else None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)

        if not (self._home or self._work):
            hint = QLabel(
                "Add where you live and where you work to your profile (Edit → Location) "
                "to see how far this home is from each."
            )
            hint.setWordWrap(True)
            hint.setStyleSheet("color: #888; font-size: 12px; padding: 12px;")
            layout.addWidget(hint)
            layout.addStretch()
            return

        grid = QGridLayout()
        grid.setColumnStretch(2, 1)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(4)
        self._work_value, self._work_note = self._add_row(grid, 0, "Drive to work")
        self._home_value, self._home_note = self._add_row(grid, 1, "From where you live now")
        self._current_value, _ = self._add_row(grid, 2, "Your commute today")
        layout.addLayout(grid)

        self._status = QLabel("Locating this listing...")
        self._status.setStyleSheet(NOTE_STYLE)
        self._status.setWordWrap(True)
        layout.addWidget(self._status)

        self._map = MapWidget()
        self._map.setFixedHeight(280)
        layout.addWidget(self._map)
        layout.addStretch()
        self._show_map(None, None)

        self._runner = GeoRunner(self)
        self._runner.result.connect(self._on_summary)
        # Wait briefly so arrowing quickly through listings doesn't fire a lookup for each one
        self._delay = QTimer(self)
        self._delay.setSingleShot(True)
        self._delay.timeout.connect(self._start)
        self._delay.start(400)

    def _add_row(self, grid: QGridLayout, row: int, title: str) -> tuple[QLabel, QLabel]:
        key = QLabel(title)
        key.setStyleSheet(KEY_STYLE)
        value = QLabel("—")
        value.setStyleSheet(VALUE_STYLE)
        note = QLabel()
        note.setStyleSheet(NOTE_STYLE)
        grid.addWidget(key, row, 0)
        grid.addWidget(value, row, 1)
        grid.addWidget(note, row, 2)
        return value, note

    def _start(self) -> None:
        self._runner.submit(
            geo.commute_summary, self._listing.address, self._listing.city, self._home, self._work
        )

    def _on_summary(self, summary) -> None:
        if summary is None or summary.listing is None:
            self._status.setText("Couldn't locate this address on the map.")
            if summary and summary.current:
                self._current_value.setText(geo.format_route(summary.current))
            return

        header_parts = []
        if summary.to_work:
            self._work_value.setText(geo.format_route(summary.to_work))
            header_parts.append(f"{geo.format_duration(summary.to_work.duration_min)} drive to work")
            if summary.current:
                text, verdict = geo.commute_delta(summary.to_work, summary.current)
                self._work_note.setText(text)
                self._work_note.setStyleSheet(f"color: {DELTA_COLORS[verdict]}; font-size: 11px;")
        if summary.from_home:
            self._home_value.setText(geo.format_route(summary.from_home))
            header_parts.append(f"{summary.from_home.distance_km:.0f} km from where you live now")
        if summary.current:
            self._current_value.setText(geo.format_route(summary.current))

        if summary.approximate:
            self._status.setText(
                "Street address not found — distances are measured from the centre of "
                f"{summary.listing.label}."
            )
        else:
            self._status.setText(summary.listing.label)

        self._show_map(summary.listing, summary)
        if header_parts:
            prefix = "≈ " if summary.approximate else ""
            self.summary_ready.emit(prefix + "  ·  ".join(header_parts))

    def _show_map(self, listing: Optional[geo.Place], summary: Optional[geo.CommuteSummary]) -> None:
        markers = []
        if self._work:
            markers.append(MapMarker(self._work[0], self._work[1], "Work", WORK_COLOR, "W"))
        if self._home:
            markers.append(MapMarker(self._home[0], self._home[1], "Home", HOME_COLOR, "H"))
        if listing:
            markers.append(MapMarker(listing.lat, listing.lon, "This listing", LISTING_COLOR, "★"))
        self._map.set_markers(markers)

        routes = []
        if summary:
            if summary.from_home and not summary.from_home.estimated:
                routes.append(MapRoute(summary.from_home.points, HOME_COLOR, dashed=True))
            if summary.to_work and not summary.to_work.estimated:
                routes.append(MapRoute(summary.to_work.points, WORK_COLOR))
        self._map.set_routes(routes)
        self._map.fit([(m.lat, m.lon) for m in markers], max_zoom=13)
