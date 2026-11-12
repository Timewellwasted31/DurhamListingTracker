"""Pannable dark map (OpenStreetMap tiles) with markers and routes, plus a place search box."""

from __future__ import annotations

import logging
import math
import queue
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Callable, Optional

import numpy as np
from PySide6.QtCore import Qt, QObject, Signal, QRect, QPointF, QTimer, QStringListModel
from PySide6.QtGui import QPainter, QColor, QPen, QPixmap, QImage, QFont, QPolygonF, QFontMetrics
from PySide6.QtWidgets import QWidget, QPushButton, QLineEdit, QCompleter

from listingtracker import geo

logger = logging.getLogger(__name__)

TILE_SIZE = 256
MIN_ZOOM, MAX_ZOOM = 4, 18
ATTRIBUTION = "© OpenStreetMap contributors"


def project(lat: float, lon: float, zoom: int) -> tuple[float, float]:
    """Web Mercator: lat/lon -> world pixel coordinates at a zoom level."""
    size = TILE_SIZE * (1 << zoom)
    lat = max(-85.0511, min(85.0511, lat))
    x = (lon + 180.0) / 360.0 * size
    y = (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * size
    return x, y


def unproject(x: float, y: float, zoom: int) -> tuple[float, float]:
    size = TILE_SIZE * (1 << zoom)
    lon = x / size * 360.0 - 180.0
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / size))))
    return lat, lon


def _dark_pixmap(png: bytes) -> Optional[QPixmap]:
    """Recolour a standard (light) OSM tile for the dark theme: flip lightness, keep hue."""
    image = QImage.fromData(png)
    if image.isNull():
        return None
    image = image.convertToFormat(QImage.Format.Format_RGB32)
    pixels = np.frombuffer(image.bits(), dtype=np.uint8).reshape(image.height(), image.bytesPerLine() // 4, 4)
    rgb = pixels[..., :3].astype(np.float32)
    flipped = rgb + (255.0 - rgb.max(axis=2, keepdims=True) - rgb.min(axis=2, keepdims=True))
    grey = flipped.mean(axis=2, keepdims=True)
    toned = (flipped * 0.6 + grey * 0.4) * 0.80 + 26.0  # mute colours, lift pure black to the app's dark grey
    pixels[..., :3] = np.clip(toned, 0, 255).astype(np.uint8)
    return QPixmap.fromImage(image)


class _TileStore(QObject):
    """Downloads and caches map tiles; shared by every MapWidget."""

    tile_ready = Signal()
    _loaded = Signal(object, bytes)

    MEMORY_LIMIT = 200
    MAX_PENDING = 48
    RETRY_AFTER = 20.0
    WORKERS = 4

    def __init__(self):
        super().__init__()
        self._tiles: OrderedDict[tuple[int, int, int], QPixmap] = OrderedDict()
        self._pending: set[tuple[int, int, int]] = set()
        self._failed: dict[tuple[int, int, int], float] = {}
        # Newest request first, so tiles for the current view beat ones scrolled past
        self._queue: queue.LifoQueue = queue.LifoQueue()
        self._loaded.connect(self._on_loaded)
        for _ in range(self.WORKERS):
            threading.Thread(target=self._worker, daemon=True).start()

    def peek(self, z: int, x: int, y: int) -> Optional[QPixmap]:
        return self._tiles.get((z, x, y))

    def get(self, z: int, x: int, y: int) -> Optional[QPixmap]:
        key = (z, x, y)
        pm = self._tiles.get(key)
        if pm is not None:
            self._tiles.move_to_end(key)
            return pm
        if (key not in self._pending and len(self._pending) < self.MAX_PENDING
                and time.monotonic() - self._failed.get(key, -1e9) >= self.RETRY_AFTER):
            self._pending.add(key)
            self._queue.put(key)
        return None

    def _worker(self) -> None:
        while True:
            key = self._queue.get()
            try:
                data = geo.fetch_tile(*key)
            except Exception as e:
                logger.debug("Tile %s failed: %s", key, e)
                data = b""
            try:
                self._loaded.emit(key, data)
            except RuntimeError:
                return  # application is shutting down

    def _on_loaded(self, key, data: bytes) -> None:
        self._pending.discard(key)
        pm = _dark_pixmap(data) if data else None
        if pm is None:
            self._failed[key] = time.monotonic()
            return
        self._tiles[key] = pm
        while len(self._tiles) > self.MEMORY_LIMIT:
            self._tiles.popitem(last=False)
        self._failed.pop(key, None)
        self.tile_ready.emit()


_store: Optional[_TileStore] = None


def tile_store() -> _TileStore:
    global _store
    if _store is None:
        _store = _TileStore()
    return _store


@dataclass
class MapMarker:
    lat: float
    lon: float
    label: str = ""
    color: str = "#42a5f5"
    glyph: str = ""


@dataclass
class MapRoute:
    points: list[tuple[float, float]]
    color: str = "#42a5f5"
    dashed: bool = False


class MapWidget(QWidget):
    clicked = Signal(float, float, object)  # lat, lon, global QPoint

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(240, 180)
        self._lat, self._lon = geo.DEFAULT_CENTER
        self._zoom = 9
        self._markers: list[MapMarker] = []
        self._routes: list[MapRoute] = []
        self._press_pos: Optional[QPointF] = None
        self._press_center: tuple[float, float] = (0.0, 0.0)
        self._dragging = False
        self._wheel_accum = 0
        self._fit: Optional[tuple[list, int, int]] = None

        tile_store().tile_ready.connect(self.update)

        button_style = (
            "QPushButton { background: #2a2a2a; color: #e0e0e0; border: 1px solid #555; "
            "border-radius: 4px; font-size: 16px; font-weight: bold; padding: 0; min-width: 0; }"
            "QPushButton:hover { background: #3a3a3a; }"
        )
        self._zoom_in = QPushButton("+", self)
        self._zoom_out = QPushButton("−", self)
        for btn, step in ((self._zoom_in, 1), (self._zoom_out, -1)):
            btn.setFixedSize(28, 28)
            btn.setStyleSheet(button_style)
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            btn.setAutoDefault(False)
            btn.clicked.connect(lambda _=False, s=step: self._zoom_by(s))

    # --- Public API ---

    def set_view(self, lat: float, lon: float, zoom: int) -> None:
        self._lat, self._lon = lat, lon
        self._zoom = max(MIN_ZOOM, min(MAX_ZOOM, zoom))
        self._fit = None
        self.update()

    def set_markers(self, markers: list[MapMarker]) -> None:
        self._markers = list(markers)
        self.update()

    def set_routes(self, routes: list[MapRoute]) -> None:
        self._routes = list(routes)
        self.update()

    def fit(self, points: list[tuple[float, float]], max_zoom: int = 14, padding: int = 56) -> None:
        """Keep every point in view, re-fitting on resize until the user pans or zooms."""
        points = [p for p in points if p]
        if not points:
            return
        self._fit = (points, max_zoom, padding)
        self._apply_fit()

    # --- Geometry ---

    def _origin(self) -> tuple[float, float]:
        cx, cy = project(self._lat, self._lon, self._zoom)
        return cx - self.width() / 2, cy - self.height() / 2

    def _to_screen(self, lat: float, lon: float) -> QPointF:
        left, top = self._origin()
        x, y = project(lat, lon, self._zoom)
        return QPointF(x - left, y - top)

    def _to_latlon(self, pos: QPointF) -> tuple[float, float]:
        left, top = self._origin()
        return unproject(left + pos.x(), top + pos.y(), self._zoom)

    def _apply_fit(self) -> None:
        # Before the widget is shown its size is a placeholder, so fitting would be wrong
        if not self._fit or not self.isVisible():
            return
        points, max_zoom, padding = self._fit
        zoom = max_zoom
        if len(points) == 1:
            zoom = min(max_zoom, 13)
        else:
            for zoom in range(max_zoom, MIN_ZOOM - 1, -1):
                xs, ys = zip(*(project(lat, lon, zoom) for lat, lon in points))
                if (max(xs) - min(xs) <= self.width() - 2 * padding
                        and max(ys) - min(ys) <= self.height() - 2 * padding):
                    break
        xs, ys = zip(*(project(lat, lon, zoom) for lat, lon in points))
        self._zoom = zoom
        self._lat, self._lon = unproject((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2, zoom)
        self.update()

    def _zoom_by(self, step: int, anchor: Optional[QPointF] = None) -> None:
        new_zoom = max(MIN_ZOOM, min(MAX_ZOOM, self._zoom + step))
        if new_zoom == self._zoom:
            return
        if anchor is None:
            anchor = QPointF(self.width() / 2, self.height() / 2)
        lat, lon = self._to_latlon(anchor)
        self._zoom = new_zoom
        # Keep the point under the cursor fixed while zooming
        ax, ay = project(lat, lon, new_zoom)
        cx = ax - (anchor.x() - self.width() / 2)
        cy = ay - (anchor.y() - self.height() / 2)
        self._lat, self._lon = unproject(cx, cy, new_zoom)
        self._fit = None
        self.update()

    # --- Events ---

    def resizeEvent(self, event):
        self._zoom_in.move(self.width() - 36, 8)
        self._zoom_out.move(self.width() - 36, 40)
        self._apply_fit()
        super().resizeEvent(event)

    def showEvent(self, event):
        self._apply_fit()
        super().showEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_pos = event.position()
            self._press_center = project(self._lat, self._lon, self._zoom)
            self._dragging = False

    def mouseMoveEvent(self, event):
        if self._press_pos is None:
            return
        delta = event.position() - self._press_pos
        if not self._dragging and delta.manhattanLength() > 4:
            self._dragging = True
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        if self._dragging:
            cx, cy = self._press_center
            self._lat, self._lon = unproject(cx - delta.x(), cy - delta.y(), self._zoom)
            self._fit = None
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton or self._press_pos is None:
            return
        was_dragging = self._dragging
        self._press_pos = None
        self._dragging = False
        self.unsetCursor()
        if not was_dragging:
            lat, lon = self._to_latlon(event.position())
            self.clicked.emit(lat, lon, event.globalPosition().toPoint())

    def wheelEvent(self, event):
        self._wheel_accum += event.angleDelta().y()
        steps = int(self._wheel_accum / 120)
        if steps:
            self._wheel_accum -= steps * 120
            self._zoom_by(1 if steps > 0 else -1, event.position())
        event.accept()

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#161616"))
        self._paint_tiles(p)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._paint_routes(p)
        self._paint_markers(p)
        self._paint_attribution(p)
        p.setPen(QPen(QColor("#333"), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(self.rect().adjusted(0, 0, -1, -1))

    def _paint_tiles(self, p: QPainter) -> None:
        store = tile_store()
        z = self._zoom
        n = 1 << z
        left, top = self._origin()
        for tx in range(math.floor(left / TILE_SIZE), math.floor((left + self.width()) / TILE_SIZE) + 1):
            for ty in range(math.floor(top / TILE_SIZE), math.floor((top + self.height()) / TILE_SIZE) + 1):
                if ty < 0 or ty >= n:
                    continue
                wx = tx % n
                px = round(tx * TILE_SIZE - left)
                py = round(ty * TILE_SIZE - top)
                pm = store.get(z, wx, ty)
                if pm is not None:
                    p.drawPixmap(px, py, pm)
                    continue
                # Not loaded yet: stretch a cached lower-zoom tile so the map never goes blank
                for dz in range(1, 4):
                    if z - dz < 0:
                        break
                    parent = store.peek(z - dz, wx >> dz, ty >> dz)
                    if parent is not None:
                        sub = TILE_SIZE >> dz
                        mask = (1 << dz) - 1
                        src = QRect((wx & mask) * sub, (ty & mask) * sub, sub, sub)
                        p.drawPixmap(QRect(px, py, TILE_SIZE, TILE_SIZE), parent, src)
                        break

    def _paint_routes(self, p: QPainter) -> None:
        for route in self._routes:
            if len(route.points) < 2:
                continue
            poly = QPolygonF([self._to_screen(lat, lon) for lat, lon in route.points])
            outline = QPen(QColor(0, 0, 0, 150), 6, Qt.PenStyle.SolidLine,
                           Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
            p.setPen(outline)
            p.drawPolyline(poly)
            pen = QPen(QColor(route.color), 3, Qt.PenStyle.DashLine if route.dashed else Qt.PenStyle.SolidLine,
                       Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen)
            p.drawPolyline(poly)

    def _paint_markers(self, p: QPainter) -> None:
        glyph_font = QFont(self.font())
        glyph_font.setBold(True)
        glyph_font.setPointSize(8)
        label_font = QFont(self.font())
        label_font.setPointSize(8)
        metrics = QFontMetrics(label_font)

        for m in self._markers:
            pt = self._to_screen(m.lat, m.lon)
            if not self.rect().adjusted(-40, -40, 40, 40).contains(pt.toPoint()):
                continue
            if m.label:
                text_w = metrics.horizontalAdvance(m.label)
                box = QRect(int(pt.x()) + 13, int(pt.y()) - 9, text_w + 10, 18)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(20, 20, 20, 215))
                p.drawRoundedRect(box, 4, 4)
                p.setPen(QColor("#e0e0e0"))
                p.setFont(label_font)
                p.drawText(box, Qt.AlignmentFlag.AlignCenter, m.label)
            p.setPen(QPen(QColor("#ffffff"), 2))
            p.setBrush(QColor(m.color))
            p.drawEllipse(pt, 9, 9)
            if m.glyph:
                p.setPen(QColor("#ffffff"))
                p.setFont(glyph_font)
                p.drawText(QRect(int(pt.x()) - 9, int(pt.y()) - 9, 18, 18),
                           Qt.AlignmentFlag.AlignCenter, m.glyph)

    def _paint_attribution(self, p: QPainter) -> None:
        font = QFont(self.font())
        font.setPointSize(7)
        p.setFont(font)
        text_w = QFontMetrics(font).horizontalAdvance(ATTRIBUTION)
        box = QRect(self.width() - text_w - 10, self.height() - 16, text_w + 10, 16)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(18, 18, 18, 200))
        p.drawRect(box)
        p.setPen(QColor("#999"))
        p.drawText(box, Qt.AlignmentFlag.AlignCenter, ATTRIBUTION)


class GeoRunner(QObject):
    """Runs blocking geo lookups on a background thread; only the newest request's result is delivered."""

    result = Signal(object)  # None when the lookup failed
    _done = Signal(int, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._request_id = 0
        self._done.connect(self._on_done)

    def submit(self, fn: Callable, *args) -> None:
        self._request_id += 1
        request_id = self._request_id

        def work():
            try:
                value = fn(*args)
            except Exception as e:
                logger.info("Geo lookup failed: %s", e)
                value = None
            try:
                self._done.emit(request_id, value)
            except RuntimeError:
                pass  # the widget that asked was closed while the lookup ran

        threading.Thread(target=work, daemon=True).start()

    def _on_done(self, request_id: int, value) -> None:
        if request_id == self._request_id:
            self.result.emit(value)


COMPLETER_POPUP_STYLE = (
    "QListView { background: #2a2a2a; color: #e0e0e0; border: 1px solid #555; "
    "font-size: 12px; outline: none; }"
    "QListView::item { padding: 5px 8px; }"
    "QListView::item:selected { background: #1565c0; color: #fff; }"
)


class PlaceSearchBox(QLineEdit):
    """Line edit with live address or city suggestions."""

    place_selected = Signal(object)  # geo.Place
    place_cleared = Signal()

    def __init__(self, kind: str = "address", clear_on_select: bool = False, parent=None):
        super().__init__(parent)
        self._kind = kind
        self._clear_on_select = clear_on_select
        self._place: Optional[geo.Place] = None
        self._suggestions: dict[str, geo.Place] = {}
        self.near: Optional[tuple[float, float]] = None
        self.setClearButtonEnabled(True)

        self._model = QStringListModel(self)
        self._completer = QCompleter(self._model, self)
        self._completer.setCompletionMode(QCompleter.CompletionMode.UnfilteredPopupCompletion)
        self._completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._completer.setMaxVisibleItems(8)
        self._completer.popup().setStyleSheet(COMPLETER_POPUP_STYLE)
        self.setCompleter(self._completer)
        self._completer.activated[str].connect(self._on_activated)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(350)
        self._timer.timeout.connect(self._query)
        self._runner = GeoRunner(self)
        self._runner.result.connect(self._on_results)

        self.textEdited.connect(self._on_text_edited)
        self.editingFinished.connect(self._on_editing_finished)

    def place(self) -> Optional[geo.Place]:
        return self._place

    def set_place(self, place: Optional[geo.Place]) -> None:
        self._place = place
        self.setText(place.label if place else "")
        self.setCursorPosition(0)

    def keyPressEvent(self, event):
        # Swallow Enter so it picks a suggestion instead of pressing the dialog's default button
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if self._suggestions and self.text().strip():
                self._choose(next(iter(self._suggestions.values())))
            event.accept()
            return
        super().keyPressEvent(event)

    def _on_text_edited(self, text: str) -> None:
        if len(text.strip()) >= 3:
            self._timer.start()
        else:
            self._timer.stop()
            self._suggestions = {}
            self._model.setStringList([])
        if not text.strip() and self._place is not None:
            self._place = None
            self.place_cleared.emit()

    def _query(self) -> None:
        self._runner.submit(geo.search_places, self.text(), self._kind, self.near)

    def _on_results(self, places) -> None:
        places = places or []
        self._suggestions = {p.label: p for p in places}
        self._model.setStringList(list(self._suggestions))
        if self.hasFocus() and places:
            self._completer.complete()

    def _on_activated(self, label: str) -> None:
        place = self._suggestions.get(label)
        if place:
            self._choose(place)

    def _choose(self, place: geo.Place) -> None:
        self._timer.stop()
        self._completer.popup().hide()
        self._suggestions = {}
        if self._clear_on_select:
            self._place = None
            # Deferred: the completer writes its text into the box after this slot returns
            QTimer.singleShot(0, self.clear)
        else:
            self._place = place
            QTimer.singleShot(0, lambda: (self.setText(place.label), self.setCursorPosition(0)))
        self.place_selected.emit(place)

    def _on_editing_finished(self) -> None:
        if self._clear_on_select:
            return
        text = self.text().strip()
        if not text:
            if self._place is not None:
                self._place = None
                self.place_cleared.emit()
        elif self._place and text != self._place.label:
            # Typed over a chosen place without picking a new one: keep the chosen place
            self.setText(self._place.label)
            self.setCursorPosition(0)
