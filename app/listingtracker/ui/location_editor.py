"""Location editor — where you live, where you work, and the cities you'd move to, on one map."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QListWidget, QPushButton, QMenu,
)

from listingtracker import geo
from listingtracker.profiles import BuyerProfile
from listingtracker.ui.map_widget import MapWidget, MapMarker, MapRoute, MapRegion, PlaceSearchBox, GeoRunner

HOME_COLOR = "#42a5f5"
WORK_COLOR = "#ffa726"
CITY_COLOR = "#66bb6a"
CANDIDATE_COLOR = "#78909c"

CANDIDATE_CITIES = [
    "Ajax", "Brock", "Clarington", "Oshawa", "Pickering", "Scugog", "Uxbridge", "Whitby",
    "Markham", "Toronto", "Kawartha Lakes", "Peterborough", "Cobourg", "Port Hope",
]
_CANDIDATE_SET = {c.lower() for c in CANDIDATE_CITIES}

SECTION_LABEL = "color: #81d4fa; font-size: 12px; font-weight: bold; padding-top: 6px;"
HINT_LABEL = "color: #888; font-size: 11px;"


class LocationEditor(QWidget):
    changed = Signal()

    def __init__(self, profile: BuyerProfile, parent=None):
        super().__init__(parent)
        self._home: Optional[geo.Place] = None
        self._work: Optional[geo.Place] = None
        self._city_coords: dict[str, tuple[float, float]] = {}
        self._city_boundaries: dict[str, list[list[geo.Point]]] = {}
        self._commute: Optional[geo.RouteInfo] = None
        self._pending_click: Optional[tuple[str, float, float]] = None

        self._route_runner = GeoRunner()
        self._route_runner.result.connect(self._on_commute)
        self._click_runner = GeoRunner()
        self._click_runner.result.connect(self._on_click_resolved)
        self._city_runner = GeoRunner()
        self._city_runner.result.connect(self._on_cities_located)
        self._boundary_runner = GeoRunner()
        self._boundary_runner.result.connect(self._on_boundaries_loaded)
        self._candidate_boundaries: dict[str, list[list[geo.Point]]] = {}
        self._candidate_runner = GeoRunner()
        self._candidate_runner.result.connect(self._on_candidates_loaded)
        self._warmup_runner = GeoRunner()
        self._warmup_runner.submit(geo.warm_up)

        self._build_ui()
        self._load(profile)

    def _build_ui(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)

        left = QVBoxLayout()
        left.setSpacing(4)

        home_label = QLabel("Where you live now")
        home_label.setStyleSheet(SECTION_LABEL)
        left.addWidget(home_label)
        self.home_box = PlaceSearchBox("address")
        self.home_box.setPlaceholderText("Start typing your address...")
        self.home_box.place_selected.connect(lambda place: self._set_place("home", place))
        self.home_box.place_cleared.connect(lambda: self._set_place("home", None))
        left.addWidget(self.home_box)

        work_label = QLabel("Where you work")
        work_label.setStyleSheet(SECTION_LABEL)
        left.addWidget(work_label)
        self.work_box = PlaceSearchBox("address")
        self.work_box.setPlaceholderText("Workplace name or address...")
        self.work_box.place_selected.connect(lambda place: self._set_place("work", place))
        self.work_box.place_cleared.connect(lambda: self._set_place("work", None))
        left.addWidget(self.work_box)

        self.commute_label = QLabel()
        self.commute_label.setStyleSheet(HINT_LABEL)
        self.commute_label.setWordWrap(True)
        left.addWidget(self.commute_label)

        cities_label = QLabel("Cities you'd live in (priority order)")
        cities_label.setStyleSheet(SECTION_LABEL)
        left.addWidget(cities_label)
        self.city_box = PlaceSearchBox("city", clear_on_select=True)
        self.city_box.setPlaceholderText("Type a city or town to add it...")
        self.city_box.place_selected.connect(self._on_city_chosen)
        self.city_box.enter_plain.connect(self._on_city_typed)
        left.addWidget(self.city_box)

        city_row = QHBoxLayout()
        self.cities_list = QListWidget()
        self.cities_list.setMinimumHeight(110)
        city_row.addWidget(self.cities_list, 1)

        city_buttons = QVBoxLayout()
        city_buttons.setSpacing(4)
        for text, tip, handler in (
            ("▲", "Move up (higher priority)", lambda: self._move_city(-1)),
            ("▼", "Move down (lower priority)", lambda: self._move_city(1)),
            ("✕", "Remove city", self._remove_city),
        ):
            btn = QPushButton(text)
            btn.setFixedSize(28, 28)
            btn.setToolTip(tip)
            btn.setStyleSheet("QPushButton { font-size: 13px; padding: 0; min-width: 0; }")
            btn.setAutoDefault(False)
            btn.clicked.connect(handler)
            city_buttons.addWidget(btn)
        city_buttons.addStretch()
        city_row.addLayout(city_buttons)
        left.addLayout(city_row, 1)

        self.status_label = QLabel("Tip: hover over the map to see city areas, then click one to add it.")
        self.status_label.setStyleSheet(HINT_LABEL)
        self.status_label.setWordWrap(True)
        left.addWidget(self.status_label)

        left_widget = QWidget()
        left_widget.setObjectName("plainPanel")
        left_widget.setLayout(left)
        left_widget.setFixedWidth(300)
        root.addWidget(left_widget)

        self.map = MapWidget()
        self.map.setMinimumSize(360, 320)
        self.map.clicked.connect(self._on_map_clicked)
        self.map.region_clicked.connect(self._on_region_clicked)
        root.addWidget(self.map, 1)

    # --- Load / save ---

    def _load(self, profile: BuyerProfile) -> None:
        home = profile.home_point()
        if home:
            self._home = geo.Place(label=profile.home_address or "Home", lat=home[0], lon=home[1])
            self.home_box.set_place(self._home)
        work = profile.work_point()
        if work:
            self._work = geo.Place(label=profile.work_address or "Work", lat=work[0], lon=work[1])
            self.work_box.set_place(self._work)

        for city in profile.cities:
            self.cities_list.addItem(city)
            coords = profile.city_coords.get(city)
            if coords and len(coords) == 2:
                self._city_coords[city] = (coords[0], coords[1])

        missing = [c for c in profile.cities if c not in self._city_coords]
        if missing:
            self._city_runner.submit(geo.locate_cities, missing)

        if profile.cities:
            self._boundary_runner.submit(geo.fetch_city_boundaries, list(profile.cities))

        current = {c.lower() for c in profile.cities}
        candidates = [c for c in CANDIDATE_CITIES if c.lower() not in current]
        if candidates:
            self._candidate_runner.submit(geo.fetch_city_boundaries, candidates)

        self._refresh_map(fit=True)
        self._update_commute()

    def cities(self) -> list[str]:
        return [self.cities_list.item(i).text() for i in range(self.cities_list.count())]

    def apply_to(self, profile: BuyerProfile) -> None:
        profile.home_address = self._home.label if self._home else ""
        profile.home_lat = self._home.lat if self._home else 0.0
        profile.home_lon = self._home.lon if self._home else 0.0
        profile.work_address = self._work.label if self._work else ""
        profile.work_lat = self._work.lat if self._work else 0.0
        profile.work_lon = self._work.lon if self._work else 0.0
        profile.cities = self.cities()
        profile.city_coords = {
            city: [self._city_coords[city][0], self._city_coords[city][1]]
            for city in profile.cities if city in self._city_coords
        }

    # --- Home / work ---

    def _set_place(self, which: str, place: Optional[geo.Place]) -> None:
        if which == "home":
            self._home = place
            self.home_box.set_place(place)
        else:
            self._work = place
            self.work_box.set_place(place)
        # Bias later suggestions toward where the buyer already is
        anchor = self._home or self._work
        for box in (self.home_box, self.work_box, self.city_box):
            box.near = anchor.point() if anchor else None
        self._commute = None
        self._refresh_map(fit=place is not None)
        self._update_commute()
        self.changed.emit()

    def _update_commute(self) -> None:
        if self._home and self._work:
            self.commute_label.setText("Working out your current commute...")
            self._route_runner.submit(geo.driving_route, self._home.point(), self._work.point())
        else:
            self.commute_label.setText("")

    def _on_commute(self, route) -> None:
        if not (self._home and self._work) or route is None:
            self.commute_label.setText("")
            return
        self._commute = route
        self.commute_label.setText(f"Current commute: {geo.format_route(route)}")
        self._refresh_map(fit=False)

    # --- Cities ---

    def _add_city(self, name: str, point: Optional[tuple[float, float]]) -> None:
        existing = {c.lower() for c in self.cities()}
        if name.lower() in existing:
            self.status_label.setText(f"{name} is already on your list.")
            return
        self.cities_list.addItem(name)
        if point:
            self._city_coords[name] = point
        for key in list(self._candidate_boundaries):
            if key.lower() == name.lower():
                self._city_boundaries.setdefault(name, self._candidate_boundaries.pop(key))
                break
        self.status_label.setText(f"Added {name}.")
        self._refresh_map(fit=True)
        self.changed.emit()

    def _on_city_chosen(self, place: geo.Place) -> None:
        self._add_city(place.name, place.point())
        self._fetch_missing_boundaries()

    def _on_city_typed(self, text: str) -> None:
        name = text.strip().title()
        if not name:
            return
        self._add_city(name, None)
        self.city_box.clear()
        self._city_runner.submit(geo.locate_cities, [name])

    def _remove_city(self) -> None:
        row = self.cities_list.currentRow()
        if row < 0:
            return
        name = self.cities_list.takeItem(row).text()
        self._city_coords.pop(name, None)
        boundary = self._city_boundaries.pop(name, None)
        if boundary and name.lower() in _CANDIDATE_SET:
            self._candidate_boundaries[name] = boundary
        self._refresh_map(fit=False)
        self.changed.emit()

    def _move_city(self, step: int) -> None:
        row = self.cities_list.currentRow()
        target = row + step
        if row < 0 or target < 0 or target >= self.cities_list.count():
            return
        item = self.cities_list.takeItem(row)
        self.cities_list.insertItem(target, item)
        self.cities_list.setCurrentRow(target)
        self._refresh_map(fit=False)
        self.changed.emit()

    def _on_cities_located(self, found) -> None:
        if not found:
            return
        current = set(self.cities())
        for city, point in found.items():
            if city in current:
                self._city_coords[city] = point
        self._fetch_missing_boundaries()
        self._refresh_map(fit=True)

    def _fetch_missing_boundaries(self) -> None:
        missing = [c for c in self.cities() if c not in self._city_boundaries]
        if missing:
            self._boundary_runner.submit(geo.fetch_city_boundaries, missing)

    def _on_boundaries_loaded(self, found) -> None:
        if not found:
            return
        for city, boundary in found.items():
            self._city_boundaries[city] = boundary
        self._refresh_map(fit=True)

    def _on_candidates_loaded(self, found) -> None:
        if not found:
            return
        current = {c.lower() for c in self.cities()}
        for city, boundary in found.items():
            if city.lower() not in current:
                self._candidate_boundaries[city] = boundary
        self._refresh_map(fit=False)

    def _on_region_clicked(self, label: str) -> None:
        """User clicked a candidate region on the map."""
        name = label
        boundary = None
        for key in self._candidate_boundaries:
            if key.lower() == name.lower():
                boundary = self._candidate_boundaries[key]
                break
        ring = (boundary[0] if boundary else [])
        point = None
        if ring:
            point = (sum(p[0] for p in ring) / len(ring), sum(p[1] for p in ring) / len(ring))
        self._add_city(name, point)
        if not point:
            self._city_runner.submit(geo.locate_cities, [name])

    # --- Map ---

    def _refresh_map(self, fit: bool) -> None:
        markers = []
        for index, city in enumerate(self.cities(), start=1):
            point = self._city_coords.get(city)
            if point:
                markers.append(MapMarker(point[0], point[1], city.title(), CITY_COLOR, str(index)))
        if self._work:
            markers.append(MapMarker(self._work.lat, self._work.lon, "Work", WORK_COLOR, "W"))
        if self._home:
            markers.append(MapMarker(self._home.lat, self._home.lon, "Home", HOME_COLOR, "H"))
        self.map.set_markers(markers)

        regions = []
        for city in self.cities():
            boundary = self._city_boundaries.get(city)
            if boundary:
                regions.append(MapRegion(rings=boundary, color=CITY_COLOR, label=city.title()))
        self.map.set_regions(regions)

        current_cities = {c.lower() for c in self.cities()}
        candidate_regions = []
        for city, boundary in self._candidate_boundaries.items():
            if city.lower() not in current_cities:
                candidate_regions.append(MapRegion(rings=boundary, color=CANDIDATE_COLOR, label=city.title()))
        self.map.set_candidate_regions(candidate_regions)

        routes = []
        if self._commute and self._home and self._work and not self._commute.estimated:
            routes.append(MapRoute(self._commute.points, HOME_COLOR))
        self.map.set_routes(routes)

        if fit and markers:
            fit_points = [(m.lat, m.lon) for m in markers]
            for region in regions:
                for ring in region.rings:
                    fit_points.extend(ring)
            self.map.fit(fit_points, max_zoom=12)

    def _on_map_clicked(self, lat: float, lon: float, global_pos) -> None:
        menu = QMenu(self)
        home_action = menu.addAction("Set as where I live")
        work_action = menu.addAction("Set as where I work")
        city_action = menu.addAction("Add the city here to my list")
        chosen = menu.exec(global_pos)
        action = {home_action: "home", work_action: "work", city_action: "city"}.get(chosen)
        if not action:
            return
        self._pending_click = (action, lat, lon)
        self.status_label.setText("Looking up that spot...")
        self._click_runner.submit(geo.reverse_place, lat, lon)

    def _on_click_resolved(self, place) -> None:
        if not self._pending_click:
            return
        action, lat, lon = self._pending_click
        self._pending_click = None
        if place is None:
            self.status_label.setText("Couldn't look up that spot. Check your connection and try again.")
            return
        if action == "city":
            if place.city:
                self._add_city(place.city, (lat, lon))
            else:
                self.status_label.setText("No city found at that spot. Try clicking closer to a town.")
            return
        self.status_label.setText(f"Set {'home' if action == 'home' else 'work'} to {place.label}.")
        self._set_place(action, place)
