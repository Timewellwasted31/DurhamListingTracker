"""Map overview — all listings as pins on a pannable map with toggleable amenity layers."""

from __future__ import annotations

import logging
from typing import Optional

from PySide6.QtCore import Qt, Signal, QObject, QThread, QTimer
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
)

from listingtracker import geo, amenities
from listingtracker.amenities import CATEGORIES, DEFAULT_LAYERS, Amenity
from listingtracker.models import Listing, Snapshot, Status
from listingtracker.profiles import BuyerProfile
from listingtracker.finance import FinanceConfig, monthly_carrying_cost, fee_per_sqft
from listingtracker.search import classify_listing
from listingtracker.ui.map_widget import MapWidget, MapMarker

logger = logging.getLogger(__name__)

LISTING_COLOR = "#42a5f5"
SOLD_COLOR = "#ef5350"
WATCHING_COLOR = "#ffa726"


def _format_price_short(price: float) -> str:
    if price >= 1_000_000:
        return f"${price / 1_000_000:.1f}M"
    return f"${price / 1_000:.0f}K"


class _GeocodeWorker(QObject):
    located = Signal(int, float, float)
    finished = Signal()

    def __init__(self, listings: list[Listing]):
        super().__init__()
        self.listings = listings

    def run(self):
        count = 0
        for listing in self.listings:
            if not listing.address and not listing.city:
                continue
            try:
                place, _ = geo.geocode_listing(listing.address, listing.city)
                if place:
                    count += 1
                    self.located.emit(listing.id, place.lat, place.lon)
            except Exception:
                pass
        self.finished.emit()


class _AmenityWorker(QObject):
    result = Signal(str, list)
    finished = Signal()

    def __init__(self, categories: list[str], bbox: tuple):
        super().__init__()
        self.categories = categories
        self.bbox = bbox

    def run(self):
        for key in self.categories:
            items = amenities.query_bbox(key, *self.bbox)
            self.result.emit(key, items)
        self.finished.emit()


class MapOverview(QWidget):
    listing_selected = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._listings: list[Listing] = []
        self._snapshots: dict[int, Optional[Snapshot]] = {}
        self._profile: Optional[BuyerProfile] = None
        self._finance_cfg: Optional[FinanceConfig] = None
        self._listing_locs: dict[int, tuple[float, float]] = {}
        self._amenity_cache: dict[str, list[Amenity]] = {}
        self._active_layers: set[str] = set(DEFAULT_LAYERS)
        self._selected_id: Optional[int] = None
        self._geocode_thread: Optional[QThread] = None
        self._geocode_worker: Optional[_GeocodeWorker] = None
        self._amenity_thread: Optional[QThread] = None
        self._amenity_worker: Optional[_AmenityWorker] = None
        self._bbox: Optional[tuple[float, float, float, float]] = None
        self._needs_fit = True
        self._dirty = True

        self._marker_timer = QTimer(self)
        self._marker_timer.setSingleShot(True)
        self._marker_timer.setInterval(100)
        self._marker_timer.timeout.connect(self._update_markers)

        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Layer toggle bar
        bar = QWidget()
        bar.setStyleSheet("background: #1a1a1a; border-bottom: 1px solid #2a2a2a;")
        bar_layout = QHBoxLayout(bar)
        bar_layout.setContentsMargins(12, 6, 12, 6)

        lbl = QLabel("Show nearby:")
        lbl.setStyleSheet("color: #888; font-size: 11px;")
        bar_layout.addWidget(lbl)

        self._layer_btns: dict[str, QPushButton] = {}
        for key, cat in CATEGORIES.items():
            btn = QPushButton(cat.label)
            btn.setCheckable(True)
            btn.setChecked(key in self._active_layers)
            btn.setStyleSheet(
                f"QPushButton {{ background: #2a2a2a; color: #aaa; border: 1px solid #444; "
                f"border-radius: 12px; padding: 4px 14px; font-size: 11px; }}"
                f"QPushButton:checked {{ background: {cat.color}; color: #fff; border-color: {cat.color}; }}"
                f"QPushButton:hover {{ border-color: {cat.color}; }}"
            )
            btn.clicked.connect(lambda checked, k=key: self._toggle_layer(k, checked))
            bar_layout.addWidget(btn)
            self._layer_btns[key] = btn

        bar_layout.addStretch()
        self._status_label = QLabel()
        self._status_label.setStyleSheet("color: #666; font-size: 11px;")
        bar_layout.addWidget(self._status_label)
        layout.addWidget(bar)

        # Map
        self._map = MapWidget()
        self._map.clicked.connect(self._on_map_clicked)
        layout.addWidget(self._map, 1)

        # Info bar
        info = QWidget()
        info.setStyleSheet("background: #1a1a2e; border-top: 1px solid #333;")
        info_layout = QHBoxLayout(info)
        info_layout.setContentsMargins(12, 8, 12, 8)

        self._info_label = QLabel("Click a listing pin to see details")
        self._info_label.setStyleSheet("color: #888; font-size: 12px;")
        info_layout.addWidget(self._info_label, 1)

        self._details_btn = QPushButton("View Details")
        self._details_btn.setStyleSheet(
            "QPushButton { background: #1565c0; color: #fff; border: none; "
            "border-radius: 3px; padding: 5px 14px; font-size: 11px; font-weight: bold; }"
            "QPushButton:hover { background: #1976d2; }"
        )
        self._details_btn.setVisible(False)
        self._details_btn.clicked.connect(self._view_details)
        info_layout.addWidget(self._details_btn)

        layout.addWidget(info)

    # --- Public API ---

    def set_finance_config(self, cfg: FinanceConfig):
        self._finance_cfg = cfg

    def set_data(self, listings: list[Listing],
                 snapshots: dict[int, Optional[Snapshot]],
                 profile: Optional[BuyerProfile]):
        self._listings = listings
        self._snapshots = snapshots
        self._profile = profile

        if profile:
            self._active_layers = set(profile.map_amenity_layers)
            for key, btn in self._layer_btns.items():
                btn.setChecked(key in self._active_layers)

        self._dirty = True
        if self.isVisible():
            self._refresh()

    def showEvent(self, event):
        super().showEvent(event)
        if self._dirty:
            self._refresh()

    # --- Internal ---

    def _refresh(self):
        if self._geocode_thread and self._geocode_thread.isRunning():
            return
        self._dirty = False
        self._listing_locs.clear()
        self._amenity_cache.clear()
        self._selected_id = None
        self._needs_fit = True
        self._info_label.setText("Locating listings...")
        self._info_label.setTextFormat(Qt.TextFormat.PlainText)
        self._details_btn.setVisible(False)
        self._status_label.setText("Geocoding...")
        self._start_geocoding()

    def _start_geocoding(self):
        if self._geocode_thread and self._geocode_thread.isRunning():
            return
        thread = QThread()
        worker = _GeocodeWorker(self._listings)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.located.connect(self._on_listing_located)
        worker.finished.connect(self._on_geocoding_done)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(lambda: setattr(self, "_geocode_thread", None))
        thread.finished.connect(lambda: setattr(self, "_geocode_worker", None))
        self._geocode_worker = worker
        self._geocode_thread = thread
        thread.start()

    def _on_listing_located(self, listing_id: int, lat: float, lon: float):
        self._listing_locs[listing_id] = (lat, lon)
        self._marker_timer.start()

    def _on_geocoding_done(self):
        n = len(self._listing_locs)
        self._status_label.setText(f"{n} listing{'s' if n != 1 else ''}")
        self._info_label.setText(
            f"{n} listing{'s' if n != 1 else ''} on map — click a pin for details"
        )

        if self._needs_fit and self._listing_locs:
            self._map.fit(list(self._listing_locs.values()), max_zoom=13)
            self._needs_fit = False

        self._compute_bbox()
        if self._active_layers:
            self._load_amenities()

        self._update_markers()

    def _compute_bbox(self):
        lats: list[float] = []
        lons: list[float] = []
        for lat, lon in self._listing_locs.values():
            lats.append(lat)
            lons.append(lon)
        if self._profile and self._profile.city_coords:
            for coords in self._profile.city_coords.values():
                lats.append(coords[0])
                lons.append(coords[1])
        if not lats:
            self._bbox = None
            return
        pad = 0.02
        self._bbox = (min(lats) - pad, min(lons) - pad,
                      max(lats) + pad, max(lons) + pad)

    def _load_amenities(self):
        if not self._bbox or not self._active_layers:
            return
        needed = [k for k in self._active_layers if k not in self._amenity_cache]
        if not needed:
            return
        if self._amenity_thread and self._amenity_thread.isRunning():
            return
        self._status_label.setText("Loading amenities...")
        thread = QThread()
        worker = _AmenityWorker(needed, self._bbox)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.result.connect(self._on_amenity_result)
        worker.finished.connect(self._on_amenity_done)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(lambda: setattr(self, "_amenity_thread", None))
        thread.finished.connect(lambda: setattr(self, "_amenity_worker", None))
        self._amenity_worker = worker
        self._amenity_thread = thread
        thread.start()

    def _on_amenity_result(self, category: str, items: list):
        self._amenity_cache[category] = items
        self._marker_timer.start()

    def _on_amenity_done(self):
        n = len(self._listing_locs)
        self._status_label.setText(f"{n} listing{'s' if n != 1 else ''}")
        self._update_markers()

    def _toggle_layer(self, key: str, checked: bool):
        if checked:
            self._active_layers.add(key)
        else:
            self._active_layers.discard(key)
        if checked and key not in self._amenity_cache and self._bbox:
            self._load_amenities()
        else:
            self._update_markers()

    def _update_markers(self):
        markers: list[MapMarker] = []

        for key in self._active_layers:
            items = self._amenity_cache.get(key, [])
            cat = CATEGORIES.get(key)
            if not cat:
                continue
            for a in items:
                markers.append(MapMarker(
                    lat=a.lat, lon=a.lon,
                    label="", color=cat.color,
                    glyph=cat.glyph, radius=6,
                ))

        for listing in self._listings:
            loc = self._listing_locs.get(listing.id)
            if not loc:
                continue
            snap = self._snapshots.get(listing.id)
            color = self._listing_color(listing, snap)
            price_label = ""
            if snap and snap.list_price:
                price_label = _format_price_short(snap.list_price)

            hover: list[str] = []
            addr = listing.address or "Unknown"
            if listing.unit:
                addr = f"{listing.unit} — {addr}"
            hover.append(addr)
            if listing.city:
                hover.append(listing.city.title())
            if snap and snap.list_price:
                hover.append(f"${snap.list_price:,.0f}")
            parts: list[str] = []
            if snap and snap.beds:
                parts.append(f"{snap.beds} bed")
            if snap and snap.baths:
                parts.append(f"{snap.baths} bath")
            if snap and snap.sqft_min:
                parts.append(f"{snap.sqft_min} sqft")
            if parts:
                hover.append("  ·  ".join(parts))
            if snap and snap.status:
                status_val = snap.status.value if isinstance(snap.status, Status) else str(snap.status)
                if status_val:
                    hover.append(status_val.replace("_", " ").title())

            markers.append(MapMarker(
                lat=loc[0], lon=loc[1],
                label=price_label, color=color,
                glyph="", radius=9,
                hover_text=hover,
            ))

        self._map.set_markers(markers)

    def _listing_color(self, listing: Listing, snap: Optional[Snapshot]) -> str:
        if snap:
            status = snap.status.value if isinstance(snap.status, Status) else ""
            if status in ("SOLD", "SOLD_CONDITIONAL"):
                return SOLD_COLOR
            if status == "BLOCKED":
                return WATCHING_COLOR
        if self._profile and snap and snap.list_price and self._finance_cfg:
            mcc = monthly_carrying_cost(
                snap.list_price, snap.maintenance_fee or 0,
                snap.taxes_annual or 0, snap.fee_includes or "",
                self._finance_cfg,
            )
            fpsf = fee_per_sqft(snap.maintenance_fee or 0, snap.sqft_min, snap.sqft_max)
            lc = classify_listing(snap.list_price, mcc, fpsf, self._profile)
            if lc == "buffer":
                return WATCHING_COLOR
        return LISTING_COLOR

    def _on_map_clicked(self, lat: float, lon: float, global_point):
        click_pt = self._map._to_screen(lat, lon)
        best_id = None
        best_dist_sq = float("inf")

        for lid, (llat, llon) in self._listing_locs.items():
            marker_pt = self._map._to_screen(llat, llon)
            dx = click_pt.x() - marker_pt.x()
            dy = click_pt.y() - marker_pt.y()
            d = dx * dx + dy * dy
            if d < best_dist_sq:
                best_dist_sq = d
                best_id = lid

        if best_id is not None and best_dist_sq < 625:
            self._select_listing(best_id)
        else:
            self._deselect()

    def _select_listing(self, listing_id: int):
        self._selected_id = listing_id
        listing = next((l for l in self._listings if l.id == listing_id), None)
        if not listing:
            return
        snap = self._snapshots.get(listing_id)

        parts: list[str] = []
        addr = listing.address or "Unknown"
        if listing.unit:
            addr = f"{listing.unit} — {addr}"
        parts.append(f"<b>{addr}</b>")
        if listing.city:
            parts.append(listing.city.title())
        if snap and snap.list_price:
            parts.append(f"${snap.list_price:,.0f}")
        if snap and snap.beds:
            parts.append(f"{snap.beds} bed")
        if snap and snap.sqft_min:
            parts.append(f"{snap.sqft_min} sqft")

        self._info_label.setText("  ·  ".join(parts))
        self._info_label.setTextFormat(Qt.TextFormat.RichText)
        self._details_btn.setVisible(True)

    def _deselect(self):
        self._selected_id = None
        n = len(self._listing_locs)
        self._info_label.setText(
            f"{n} listing{'s' if n != 1 else ''} on map — click a pin for details"
        )
        self._info_label.setTextFormat(Qt.TextFormat.PlainText)
        self._details_btn.setVisible(False)

    def _view_details(self):
        if self._selected_id is not None:
            self.listing_selected.emit(self._selected_id)
