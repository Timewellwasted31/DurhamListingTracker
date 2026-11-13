"""Main window — sidebar listing list, expanded detail, profiles, dark theme."""

from __future__ import annotations

import csv
import logging
import threading
import time
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Optional

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QListWidget, QListWidgetItem, QPushButton, QLabel, QProgressBar,
    QCheckBox, QLineEdit, QComboBox, QInputDialog, QMessageBox,
    QFrame, QAbstractItemView, QSpinBox, QDoubleSpinBox, QGroupBox,
    QFormLayout, QApplication, QMenu, QScrollArea,
)
from PySide6.QtCore import Qt, QThread, Signal, QObject, QSize, QTimer, QRectF
from PySide6.QtGui import QColor, QIcon, QPalette, QFont, QAction, QPainter, QLinearGradient, QBrush
from PySide6.QtWidgets import QStyledItemDelegate

from listingtracker.models import Listing, Snapshot, Status, Change
from listingtracker.db import (
    get_all_listings, get_listing_by_url, get_latest_snapshot,
    get_all_latest_snapshots, get_previous_snapshot, get_snapshots_for_listing,
    add_listing, update_listing, delete_listing, cleanup_bad_listings,
    cleanup_non_purchasable, normalize_url,
)
from listingtracker.config import load_config, save_config, config_to_finance
from listingtracker.finance import (
    FinanceConfig, monthly_carrying_cost, qualifies,
    price_per_sqft, fee_per_sqft,
)
from listingtracker.fetcher import get_adapter_for_url, fetch_all, search_and_discover, enrich_listing
from listingtracker.diffing import diff_snapshots, sort_changes
from listingtracker.profiles import (
    BuyerProfile, save_profile, load_profile, list_profiles,
    delete_profile, get_active_profile_name, set_active_profile_name,
)
from listingtracker.search import classify_listing, build_search_urls, ALL_SEARCH_SITES
from listingtracker.valuescore import compute_value_metrics
from listingtracker import areanews
from listingtracker.crossref import (
    build_cross_ref_results, CrossRefResult,
    merge_from_cross_ref, build_enrichment_search_urls,
    build_enrichment_from_parsed, EnrichmentResult, is_same_property,
)
from listingtracker.ui.detail_pane import DetailPane
from listingtracker.ui.settings_dialog import SettingsDialog
from listingtracker.ui.profile_dialog import ProfileDialog
from listingtracker.paths import EXPORTS_DIR
from listingtracker.__version__ import __version__
from listingtracker.updater import check_for_update, download_installer, launch_installer, UpdateInfo

logger = logging.getLogger(__name__)


def _infer_property_type(listing: Listing, snap: Optional[Snapshot]) -> str:
    if snap and snap.property_type:
        pt = snap.property_type.lower()
        if "town" in pt:
            return "TOWN"
        if "detach" in pt and "semi" not in pt:
            return "FREE"
        if "semi" in pt:
            return "SEMI"
        if "condo" in pt or "apartment" in pt:
            return "CONDO"
    url_lower = listing.url.lower()
    if "townhouse" in url_lower or "townhome" in url_lower:
        return "TOWN"
    if snap and snap.maintenance_fee and snap.maintenance_fee > 0:
        return "CONDO"
    if "condo" in url_lower or "apartment" in url_lower:
        return "CONDO"
    if "house" in url_lower or "detached" in url_lower:
        return "FREE"
    if snap and snap.list_price:
        return "FREE"
    return ""


# Property-type color mapping: rental=red, condo=green, freehold=blue (muted for dark theme)
PTYPE_COLORS = {
    "CONDO": "#1a3a2a",
    "TOWN": "#1a2a3a",
    "FREE": "#1a2a3a",
    "SEMI": "#1a2a3a",
    "RENTAL": "#3a1a1a",
}
PTYPE_DEFAULT_COLOR = "#2a2a3e"

PTYPE_ROLE = Qt.ItemDataRole.UserRole + 10


def _ptype_color(ptype: str) -> str:
    return PTYPE_COLORS.get(ptype, PTYPE_DEFAULT_COLOR)


class _PencilDelegate(QStyledItemDelegate):
    """Draws a colored strip on the right quarter of listing items."""

    def paint(self, painter: QPainter, option, index):
        # City headers (no UserRole data) draw normally
        listing_id = index.data(Qt.ItemDataRole.UserRole)
        if listing_id is None:
            super().paint(painter, option, index)
            return

        # Draw the base item first
        super().paint(painter, option, index)

        color_hex = index.data(PTYPE_ROLE)
        if not color_hex or color_hex == PTYPE_DEFAULT_COLOR:
            return

        painter.save()
        rect = option.rect
        strip_w = rect.width() // 4
        strip_rect = QRectF(
            rect.right() - strip_w, rect.top(),
            strip_w, rect.height(),
        )

        grad = QLinearGradient(strip_rect.left(), 0, strip_rect.right(), 0)
        base = QColor(color_hex)
        transparent = QColor(base)
        transparent.setAlpha(0)
        grad.setColorAt(0.0, transparent)
        grad.setColorAt(0.5, QColor(base.red(), base.green(), base.blue(), 100))
        grad.setColorAt(1.0, QColor(base.red(), base.green(), base.blue(), 180))

        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(strip_rect, QBrush(grad))
        painter.restore()


DARK_STYLESHEET = """
QMainWindow, QWidget {
    background: #121212;
    color: #e0e0e0;
}
QSplitter::handle {
    background: #333;
    width: 1px;
}

/* Sidebar */
#sidebar {
    background: #181818;
    border-right: 1px solid #2a2a2a;
}
#sidebar QLabel {
    color: #e0e0e0;
}
#sidebarTitle {
    font-size: 14px;
    font-weight: bold;
    color: #81d4fa;
    padding: 8px 12px 4px 12px;
}

QListWidget {
    background: #181818;
    border: none;
    outline: none;
    font-size: 12px;
}
QListWidget::item {
    background: #1e1e1e;
    border-bottom: 1px solid #2a2a2a;
    border-radius: 0;
    padding: 10px 12px;
    color: #ddd;
}
QListWidget::item:selected {
    background: #263238;
    border-left: 3px solid #42a5f5;
    color: #fff;
}
QListWidget::item:hover:!selected {
    background: #222;
}

/* Buttons */
QPushButton {
    background: #2a2a2a;
    color: #e0e0e0;
    border: 1px solid #444;
    border-radius: 4px;
    padding: 5px 12px;
    font-size: 12px;
}
QPushButton:hover {
    background: #383838;
    border-color: #555;
}
QPushButton#recheckBtn {
    background: #1565c0;
    color: #fff;
    border: none;
    font-weight: bold;
    padding: 6px 16px;
}
QPushButton#recheckBtn:hover {
    background: #1976d2;
}
QPushButton#recheckBtn:disabled {
    background: #444;
    color: #888;
}
QPushButton#addBtn {
    background: #2e7d32;
    color: #fff;
    border: none;
}
QPushButton#addBtn:hover {
    background: #388e3c;
}
QPushButton#searchBtn {
    background: #6a1b9a;
    color: #fff;
    border: none;
}
QPushButton#searchBtn:hover {
    background: #7b1fa2;
}

/* Toolbar */
#toolbar {
    background: #181818;
    border-bottom: 1px solid #2a2a2a;
    padding: 6px 12px;
}

/* Profile bar */
#profileBar {
    background: #1a1a1a;
    border-bottom: 1px solid #2a2a2a;
    padding: 4px 12px;
}

/* Progress */
QProgressBar {
    background: #2a2a2a;
    border: none;
    border-radius: 3px;
    height: 4px;
    text-align: center;
    color: transparent;
}
QProgressBar::chunk {
    background: #42a5f5;
    border-radius: 3px;
}

/* Changes panel */
#changesPanel {
    background: #1a1a2e;
    border: 1px solid #3a3a5c;
    border-radius: 6px;
    margin: 8px 12px;
    padding: 10px 14px;
}

/* Status bar */
QStatusBar {
    background: #181818;
    color: #888;
    border-top: 1px solid #2a2a2a;
    font-size: 11px;
}

/* Checkbox */
QCheckBox {
    color: #ccc;
    font-size: 12px;
    spacing: 4px;
}
QCheckBox::indicator {
    width: 14px;
    height: 14px;
    border: 1px solid #555;
    border-radius: 3px;
    background: #2a2a2a;
}
QCheckBox::indicator:checked {
    background: #42a5f5;
    border-color: #42a5f5;
}

/* Filter combos and spinboxes */
QComboBox, QSpinBox, QDoubleSpinBox {
    background: #2a2a2a;
    color: #ddd;
    border: 1px solid #444;
    border-radius: 3px;
    padding: 3px 6px;
    font-size: 12px;
}
QComboBox::drop-down {
    border: none;
    width: 18px;
}
QComboBox QAbstractItemView {
    background: #2a2a2a;
    color: #ddd;
    selection-background-color: #42a5f5;
}

/* Scroll bars */
QScrollBar:vertical {
    background: #181818;
    width: 8px;
    margin: 0;
}
QScrollBar::handle:vertical {
    background: #444;
    border-radius: 4px;
    min-height: 30px;
}
QScrollBar::handle:vertical:hover {
    background: #555;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
    background: none;
    border: none;
    height: 0;
}

/* Message boxes / dialogs */
QMessageBox, QInputDialog {
    background: #1e1e1e;
    color: #e0e0e0;
}

/* Menu */
QMenu {
    background: #2a2a2a;
    color: #ddd;
    border: 1px solid #444;
}
QMenu::item:selected {
    background: #42a5f5;
}
"""


class FetchWorker(QObject):
    progress = Signal(int, int, str)
    finished = Signal(list, list)
    error = Signal(str)

    def __init__(self, listings, show_browser):
        super().__init__()
        self.listings = listings
        self.show_browser = show_browser
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            def on_progress(i, total, address):
                self.progress.emit(i, total, address)

            snaps, changes = fetch_all(
                self.listings,
                show_browser=self.show_browser,
                progress_callback=on_progress,
                cancel_check=lambda: self._cancelled,
            )
            self.finished.emit(snaps, changes)
        except Exception as e:
            logger.error("Fetch failed: %s", e, exc_info=True)
            self.error.emit(str(e))


class SearchWorker(QObject):
    progress = Signal(int, int, str)
    finished = Signal(list)
    error = Signal(str)

    def __init__(self, search_urls, show_browser):
        super().__init__()
        self.search_urls = search_urls
        self.show_browser = show_browser
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            def on_progress(i, total, desc):
                self.progress.emit(i, total, desc)

            urls = search_and_discover(
                self.search_urls,
                show_browser=self.show_browser,
                progress_callback=on_progress,
                cancel_check=lambda: self._cancelled,
            )
            self.finished.emit(urls)
        except Exception as e:
            logger.error("Search failed: %s", e, exc_info=True)
            self.error.emit(str(e))


class HistoryFetchWorker(QObject):
    finished = Signal(int, int)  # listing_id, total snapshot count
    error = Signal(str)

    def __init__(self, listing, show_browser):
        super().__init__()
        self.listing = listing
        self.show_browser = show_browser

    def run(self):
        try:
            from patchright.sync_api import sync_playwright
            from listingtracker.fetcher import (
                get_adapter_for_url, fetch_listing, _clean_browser_locks,
                _random_viewport,
            )
            from listingtracker.db import get_snapshots_for_listing

            adapter = get_adapter_for_url(self.listing.url)
            if not adapter:
                self.error.emit(f"No adapter for {self.listing.url}")
                return

            _clean_browser_locks()

            with sync_playwright() as p:
                browser = p.chromium.launch(
                    headless=not self.show_browser,
                    args=[
                        "--disable-blink-features=AutomationControlled",
                        "--no-first-run",
                        "--no-default-browser-check",
                    ],
                )
                vw, vh = _random_viewport()
                context = browser.new_context(viewport={"width": vw, "height": vh})
                page = context.new_page()

                snap, _ = fetch_listing(
                    self.listing, page, adapter, listing_index=0,
                )

                page.close()
                context.close()
                browser.close()

            all_snaps = get_snapshots_for_listing(self.listing.id)
            self.finished.emit(self.listing.id, len(all_snaps))
        except Exception as e:
            logger.error("History fetch failed: %s", e, exc_info=True)
            self.error.emit(str(e))


class EnrichWorker(QObject):
    progress = Signal(str)
    finished = Signal(int, list)  # listing_id, list of {site, url, parsed}
    error = Signal(str)

    def __init__(self, listing, current_snap, search_urls, show_browser):
        super().__init__()
        self.listing = listing
        self.current_snap = current_snap
        self.search_urls = search_urls
        self.show_browser = show_browser

    def run(self):
        try:
            from listingtracker.fetcher import enrich_listing_safe
            logger.info("EnrichWorker: starting enrich_listing_safe for listing %d", self.listing.id)
            results = enrich_listing_safe(
                self.listing,
                self.current_snap,
                self.search_urls,
                show_browser=self.show_browser,
                progress_callback=lambda desc: self.progress.emit(desc),
            )
            logger.info("EnrichWorker: enrich_listing_safe returned %d results", len(results))
            self.finished.emit(self.listing.id, results)
        except Exception as e:
            logger.error("Enrichment failed: %s", e, exc_info=True)
            self.error.emit(str(e))


class UpdateCheckWorker(QObject):
    finished = Signal(object)  # UpdateInfo or None

    def run(self):
        result = check_for_update()
        self.finished.emit(result)


class _DownloadWorker(QObject):
    progress = Signal(int, int)  # downloaded, total
    finished = Signal(str)       # path to installer
    error = Signal(str)

    def __init__(self, url: str):
        super().__init__()
        self.url = url

    def run(self):
        try:
            path = download_installer(
                self.url,
                progress_callback=lambda d, t: self.progress.emit(d, t),
            )
            self.finished.emit(str(path))
        except Exception as e:
            self.error.emit(str(e))


class MainWindow(QMainWindow):
    _area_news_ready = Signal(int, object)  # listing id, AreaNews (None if the lookup crashed)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Durham Listing Tracker")
        self.setMinimumSize(1200, 700)
        self.setStyleSheet(DARK_STYLESHEET)

        self.config = load_config()
        self.finance_cfg = config_to_finance(self.config)
        self._fetch_thread: Optional[QThread] = None
        self._search_thread: Optional[QThread] = None
        self._enrich_thread: Optional[QThread] = None
        self._history_thread: Optional[QThread] = None
        self._research_listing_id: Optional[int] = None
        self._research_pending: set[str] = set()  # of "news", "sites", "history"
        self._area_news_ready.connect(self._on_area_news)
        self._listings_cache: list[Listing] = []
        self._listing_snapshots: dict[int, Optional[Snapshot]] = {}
        self._active_profile: Optional[BuyerProfile] = None
        self._cross_ref_results: list[CrossRefResult] = []
        self._cross_ref_index: dict[int, CrossRefResult] = {}
        self._op_start_time: float = 0.0
        self._op_progress_text: str = ""
        self._elapsed_timer = QTimer(self)
        self._elapsed_timer.setInterval(1000)
        self._elapsed_timer.timeout.connect(self._update_elapsed)

        self._update_thread: Optional[QThread] = None
        self._update_info: Optional[UpdateInfo] = None

        self._build_ui()
        self._load_active_profile()
        self._load_listings()
        if not list_profiles():
            QTimer.singleShot(0, self._run_setup_wizard)

        QTimer.singleShot(3000, self._check_for_updates)

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Profile bar
        profile_bar = QWidget()
        profile_bar.setObjectName("profileBar")
        pb_layout = QHBoxLayout(profile_bar)
        pb_layout.setContentsMargins(12, 4, 12, 4)

        pb_label = QLabel("Profile:")
        pb_label.setStyleSheet("color: #888; font-size: 11px;")
        pb_layout.addWidget(pb_label)

        self.profile_combo = QComboBox()
        self.profile_combo.setFixedWidth(200)
        self.profile_combo.setFixedHeight(24)
        self.profile_combo.currentTextChanged.connect(self._on_profile_changed)
        pb_layout.addWidget(self.profile_combo)

        self.new_profile_btn = QPushButton("New")
        self.new_profile_btn.setFixedHeight(24)
        self.new_profile_btn.clicked.connect(self._new_profile)
        pb_layout.addWidget(self.new_profile_btn)

        self.edit_profile_btn = QPushButton("Edit")
        self.edit_profile_btn.setFixedHeight(24)
        self.edit_profile_btn.clicked.connect(self._edit_profile)
        pb_layout.addWidget(self.edit_profile_btn)

        self.delete_profile_btn = QPushButton("Delete")
        self.delete_profile_btn.setFixedHeight(24)
        self.delete_profile_btn.setStyleSheet(
            "QPushButton { color: #ef5350; } QPushButton:hover { background: #3a1a1a; }"
        )
        self.delete_profile_btn.clicked.connect(self._delete_profile)
        pb_layout.addWidget(self.delete_profile_btn)

        pb_layout.addStretch()

        self.profile_summary = QLabel()
        self.profile_summary.setStyleSheet("color: #666; font-size: 11px;")
        pb_layout.addWidget(self.profile_summary)

        root.addWidget(profile_bar)

        # Toolbar
        toolbar = QWidget()
        toolbar.setObjectName("toolbar")
        tb_layout = QHBoxLayout(toolbar)
        tb_layout.setContentsMargins(12, 6, 12, 6)

        self.recheck_btn = QPushButton("Recheck All")
        self.recheck_btn.setObjectName("recheckBtn")
        self.recheck_btn.clicked.connect(self._recheck_all)
        tb_layout.addWidget(self.recheck_btn)

        self.add_btn = QPushButton("+ Add Listing")
        self.add_btn.setObjectName("addBtn")
        self.add_btn.clicked.connect(self._add_listing)
        tb_layout.addWidget(self.add_btn)

        self.search_btn = QPushButton("Auto-Search")
        self.search_btn.setObjectName("searchBtn")
        self.search_btn.clicked.connect(self._auto_search)
        tb_layout.addWidget(self.search_btn)

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setStyleSheet(
            "QPushButton { background: #b71c1c; color: #fff; border: none; "
            "font-weight: bold; padding: 6px 14px; }"
            "QPushButton:hover { background: #c62828; }"
        )
        self.cancel_btn.clicked.connect(self._cancel_operation)
        self.cancel_btn.setVisible(False)
        tb_layout.addWidget(self.cancel_btn)

        self.settings_btn = QPushButton("Settings")
        self.settings_btn.clicked.connect(self._open_settings)
        tb_layout.addWidget(self.settings_btn)

        self.export_btn = QPushButton("Export CSV")
        self.export_btn.clicked.connect(self._export_csv)
        tb_layout.addWidget(self.export_btn)

        self.show_browser_cb = QCheckBox("Show browser")
        self.show_browser_cb.setChecked(self.config.get("show_browser", False))
        tb_layout.addWidget(self.show_browser_cb)

        tb_layout.addStretch()

        self.last_refresh_label = QLabel("Last refresh: never")
        self.last_refresh_label.setStyleSheet("color: #888; font-size: 11px;")
        tb_layout.addWidget(self.last_refresh_label)

        root.addWidget(toolbar)

        # Progress bar + status label
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_bar.setFixedHeight(4)
        root.addWidget(self.progress_bar)

        self.progress_label = QLabel()
        self.progress_label.setVisible(False)
        self.progress_label.setStyleSheet(
            "color: #81d4fa; font-size: 11px; padding: 2px 12px; background: #1a1a2e;"
        )
        root.addWidget(self.progress_label)

        # Changes banner — collapsible, scrollable, dismissible
        self.changes_frame = QWidget()
        self.changes_frame.setObjectName("changesPanel")
        cf_layout = QVBoxLayout(self.changes_frame)
        cf_layout.setContentsMargins(8, 6, 8, 6)
        cf_layout.setSpacing(4)

        cf_header = QHBoxLayout()
        self.changes_label = QLabel("Changes since last check")
        self.changes_label.setStyleSheet("font-weight: bold; font-size: 13px; color: #ffa726;")
        cf_header.addWidget(self.changes_label)
        cf_header.addStretch()
        self.changes_dismiss_btn = QPushButton("X")
        self.changes_dismiss_btn.setFixedSize(22, 22)
        self.changes_dismiss_btn.setStyleSheet(
            "QPushButton { background: transparent; color: #888; border: none; "
            "font-weight: bold; font-size: 14px; }"
            "QPushButton:hover { color: #ef5350; }"
        )
        self.changes_dismiss_btn.clicked.connect(self._dismiss_changes)
        cf_header.addWidget(self.changes_dismiss_btn)
        cf_layout.addLayout(cf_header)

        self.changes_scroll = QScrollArea()
        self.changes_scroll.setWidgetResizable(True)
        self.changes_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.changes_scroll.setMaximumHeight(120)
        self.changes_scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
        )
        self.changes_content = QLabel()
        self.changes_content.setWordWrap(True)
        self.changes_content.setStyleSheet("color: #ccc; font-size: 12px;")
        self.changes_scroll.setWidget(self.changes_content)
        cf_layout.addWidget(self.changes_scroll)

        self.changes_frame.setVisible(False)
        root.addWidget(self.changes_frame)

        # Update banner
        self.update_frame = QWidget()
        self.update_frame.setObjectName("updatePanel")
        self.update_frame.setStyleSheet(
            "#updatePanel { background: #1a2a1a; border-bottom: 1px solid #2a4a2a; }"
        )
        uf_layout = QHBoxLayout(self.update_frame)
        uf_layout.setContentsMargins(12, 6, 12, 6)
        self.update_label = QLabel()
        self.update_label.setStyleSheet("color: #81c784; font-size: 12px;")
        uf_layout.addWidget(self.update_label)
        uf_layout.addStretch()
        self.update_btn = QPushButton("Download && Install")
        self.update_btn.setStyleSheet(
            "QPushButton { background: #2e7d32; color: #fff; border: none; "
            "font-weight: bold; padding: 4px 14px; border-radius: 3px; }"
            "QPushButton:hover { background: #388e3c; }"
        )
        self.update_btn.clicked.connect(self._do_update)
        uf_layout.addWidget(self.update_btn)
        self.update_dismiss_btn = QPushButton("X")
        self.update_dismiss_btn.setFixedSize(22, 22)
        self.update_dismiss_btn.setStyleSheet(
            "QPushButton { background: transparent; color: #888; border: none; "
            "font-weight: bold; font-size: 14px; }"
            "QPushButton:hover { color: #ef5350; }"
        )
        self.update_dismiss_btn.clicked.connect(lambda: self.update_frame.setVisible(False))
        uf_layout.addWidget(self.update_dismiss_btn)
        self.update_frame.setVisible(False)
        root.addWidget(self.update_frame)

        # Splitter: sidebar + detail
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(1)

        # Sidebar
        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(0, 0, 0, 0)
        sidebar_layout.setSpacing(0)

        sidebar_title = QLabel("LISTINGS")
        sidebar_title.setObjectName("sidebarTitle")
        sidebar_layout.addWidget(sidebar_title)

        # Filter/sort row
        filter_row = QHBoxLayout()
        filter_row.setContentsMargins(8, 4, 8, 4)
        self.sort_combo = QComboBox()
        self.sort_combo.addItems([
            "Default",
            "Price (low-high)",
            "Price (high-low)",
            "Sqft (low-high)",
            "Sqft (high-low)",
            "Monthly (low-high)",
            "Monthly (high-low)",
            "Value Score (high-low)",
            "Value Score (low-high)",
        ])
        self.sort_combo.setFixedHeight(24)
        self.sort_combo.currentTextChanged.connect(self._apply_filters)
        filter_row.addWidget(self.sort_combo)

        self.filter_qualifies = QCheckBox("Qualifies")
        self.filter_qualifies.stateChanged.connect(self._apply_filters)
        filter_row.addWidget(self.filter_qualifies)

        self.filter_pos = QCheckBox("POS")
        self.filter_pos.setToolTip("Show only Power of Sale listings")
        self.filter_pos.stateChanged.connect(self._apply_filters)
        filter_row.addWidget(self.filter_pos)

        sidebar_layout.addLayout(filter_row)

        self.listing_list = QListWidget()
        self.listing_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.listing_list.setItemDelegate(_PencilDelegate(self.listing_list))
        self.listing_list.currentRowChanged.connect(self._on_listing_selected)
        self.listing_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.listing_list.customContextMenuRequested.connect(self._listing_context_menu)
        sidebar_layout.addWidget(self.listing_list, 1)

        # Listing count
        self.listing_count_label = QLabel("0 listings")
        self.listing_count_label.setStyleSheet(
            "color: #666; font-size: 10px; padding: 4px 12px;"
        )
        sidebar_layout.addWidget(self.listing_count_label)

        sidebar.setFixedWidth(300)
        splitter.addWidget(sidebar)

        # Detail pane
        self.detail_pane = DetailPane()
        self.detail_pane.notes_saved.connect(self._on_notes_saved)
        self.detail_pane.open_in_browser.connect(self._open_url_in_browser)
        self.detail_pane.research_requested.connect(self._research_listing)
        self.detail_pane.apply_enrichment_requested.connect(self._apply_enrichment)
        self.detail_pane.fetch_history_requested.connect(self._fetch_listing_history)
        self.detail_pane.set_finance_config(self.finance_cfg)
        splitter.addWidget(self.detail_pane)

        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        root.addWidget(splitter, 1)

        version_label = QLabel(f"v{__version__}")
        version_label.setStyleSheet("color: #555; font-size: 10px; padding-right: 8px;")
        self.statusBar().addPermanentWidget(version_label)
        self.statusBar().showMessage("Ready")

    # --- Profile management ---

    def _refresh_profile_combo(self):
        self.profile_combo.blockSignals(True)
        self.profile_combo.clear()
        self.profile_combo.addItem("(No Profile)")
        names = list_profiles()
        for name in names:
            self.profile_combo.addItem(name)
        self.profile_combo.blockSignals(False)

    def _load_active_profile(self):
        self._refresh_profile_combo()
        active_name = get_active_profile_name()
        if active_name:
            profile = load_profile(active_name)
            if profile:
                self._active_profile = profile
                idx = self.profile_combo.findText(active_name)
                if idx >= 0:
                    self.profile_combo.setCurrentIndex(idx)
                self._apply_profile(profile)
                return
        self.profile_combo.setCurrentIndex(0)

    def _on_profile_changed(self, name: str):
        if name == "(No Profile)" or not name:
            self._active_profile = None
            set_active_profile_name("")
            self.detail_pane.set_profile(None)
            self._update_profile_summary()
            self._populate_list()
            return
        profile = load_profile(name)
        if profile:
            self._active_profile = profile
            set_active_profile_name(name)
            self._apply_profile(profile)
        else:
            logger.warning("Could not load profile: %s", name)
            self._active_profile = None
            self._update_profile_summary()
            self._populate_list()

    def _apply_profile(self, profile: BuyerProfile):
        fin_dict = profile.to_finance_dict()
        self.config.update(fin_dict)
        save_config(self.config)
        self.finance_cfg = config_to_finance(self.config)
        self.detail_pane.set_finance_config(self.finance_cfg)
        self.detail_pane.set_profile(profile)
        self._update_profile_summary()
        self._populate_list()

    def _update_profile_summary(self):
        p = self._active_profile
        if not p:
            self.profile_summary.setText("")
            self.setWindowTitle("Durham Listing Tracker")
            return
        parts = []
        if p.cities:
            parts.append(", ".join(c.title() for c in p.cities[:3]))
        parts.append(f"${p.price_ceiling:,.0f} ceiling")
        parts.append(f"${p.monthly_allin_ceiling:,.0f}/mo max")
        if p.monthly_allin_target > 0:
            parts.append(f"${p.monthly_allin_target:,.0f}/mo target")
        self.profile_summary.setText("  |  ".join(parts))
        self.setWindowTitle(f"Durham Listing Tracker — {p.name}")

    # -- Auto-update --------------------------------------------------------

    def _check_for_updates(self):
        if self._update_thread is not None:
            return
        self._update_thread = QThread()
        worker = UpdateCheckWorker()
        worker.moveToThread(self._update_thread)
        self._update_thread.started.connect(worker.run)
        worker.finished.connect(self._on_update_check_done)
        worker.finished.connect(self._update_thread.quit)
        worker.finished.connect(worker.deleteLater)
        self._update_thread.finished.connect(self._update_thread.deleteLater)
        self._update_thread.finished.connect(lambda: setattr(self, "_update_thread", None))
        self._update_thread.start()

    def _on_update_check_done(self, info: Optional[UpdateInfo]):
        if info is None or not info.is_newer or not info.download_url:
            return
        self._update_info = info
        self.update_label.setText(
            f"Update available: v{info.latest_version}  (you have v{info.current_version})"
        )
        self.update_frame.setVisible(True)

    def _do_update(self):
        if not self._update_info or not self._update_info.download_url:
            return
        reply = QMessageBox.question(
            self,
            "Update Available",
            f"Download and install v{self._update_info.latest_version}?\n\n"
            "The app will close and reopen automatically.\n"
            "Your data and profiles will be preserved.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self.update_btn.setEnabled(False)
        self.update_btn.setText("Downloading...")
        self._update_thread = QThread()
        worker = _DownloadWorker(self._update_info.download_url)
        worker.moveToThread(self._update_thread)
        self._update_thread.started.connect(worker.run)
        worker.progress.connect(self._on_download_progress)
        worker.finished.connect(self._on_download_done)
        worker.error.connect(self._on_download_error)
        worker.finished.connect(self._update_thread.quit)
        worker.finished.connect(worker.deleteLater)
        self._update_thread.finished.connect(self._update_thread.deleteLater)
        self._update_thread.finished.connect(lambda: setattr(self, "_update_thread", None))
        self._update_thread.start()

    def _on_download_progress(self, downloaded: int, total: int):
        if total > 0:
            pct = int(downloaded * 100 / total)
            self.update_btn.setText(f"Downloading... {pct}%")
        else:
            mb = downloaded / (1024 * 1024)
            self.update_btn.setText(f"Downloading... {mb:.1f} MB")

    def _on_download_done(self, path: str):
        self.update_btn.setText("Installing...")
        launch_installer(Path(path))

    def _on_download_error(self, msg: str):
        self.update_btn.setEnabled(True)
        self.update_btn.setText("Download && Install")
        QMessageBox.warning(self, "Update Failed", f"Could not download the update:\n{msg}")

    # -- Setup wizard / Profiles -------------------------------------------

    def _run_setup_wizard(self):
        from listingtracker.ui.setup_wizard import SetupWizard

        dialog = SetupWizard(self)
        if not dialog.exec():
            return
        profile = dialog.get_profile()
        save_profile(profile)
        self._refresh_profile_combo()
        idx = self.profile_combo.findText(profile.name)
        if idx >= 0:
            self.profile_combo.setCurrentIndex(idx)
        if dialog.run_search and self._active_profile:
            self._start_search(list(ALL_SEARCH_SITES))

    def _new_profile(self):
        profile = BuyerProfile(name="New Profile")
        dialog = ProfileDialog(profile, self, new=True)
        if dialog.exec():
            profile = dialog.get_profile()
            save_profile(profile)
            self._refresh_profile_combo()
            idx = self.profile_combo.findText(profile.name)
            if idx >= 0:
                self.profile_combo.setCurrentIndex(idx)

    def _edit_profile(self):
        if not self._active_profile:
            QMessageBox.information(self, "No Profile", "Select or create a profile first.")
            return
        dialog = ProfileDialog(self._active_profile, self)
        if dialog.exec():
            old_name = self._active_profile.name
            profile = dialog.get_profile()
            if profile.name != old_name:
                delete_profile(old_name)
            save_profile(profile)
            self._active_profile = profile
            set_active_profile_name(profile.name)
            self._refresh_profile_combo()
            idx = self.profile_combo.findText(profile.name)
            if idx >= 0:
                self.profile_combo.setCurrentIndex(idx)
            self._apply_profile(profile)

    def _delete_profile(self):
        if not self._active_profile:
            return
        name = self._active_profile.name
        reply = QMessageBox.question(
            self, "Delete Profile",
            f"Delete profile '{name}'? This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            delete_profile(name)
            self._active_profile = None
            set_active_profile_name("")
            self._refresh_profile_combo()
            self.profile_combo.blockSignals(True)
            self.profile_combo.setCurrentIndex(0)
            self.profile_combo.blockSignals(False)
            self.detail_pane.set_profile(None)
            self._update_profile_summary()
            self._populate_list()

    # --- Listing display ---

    def _load_listings(self):
        from listingtracker.fetcher import resolve_city
        cleanup_bad_listings()
        cleanup_non_purchasable()
        self._refresh_list_no_cleanup()

    def _refresh_list_no_cleanup(self):
        """Reload listing list from DB without running cleanup (safe during add→fetch)."""
        from listingtracker.fetcher import resolve_city
        self._listings_cache = get_all_listings(active_only=False)
        self._listing_snapshots = get_all_latest_snapshots()
        needs_update = []
        for listing in self._listings_cache:
            resolved = resolve_city(listing.city, listing.url)
            if resolved != listing.city:
                listing.city = resolved
                needs_update.append(listing)
        for listing in needs_update:
            update_listing(listing)
        self._populate_list()

    def _populate_list(self):
        current = self.listing_list.currentItem()
        selected_id = current.data(Qt.ItemDataRole.UserRole) if current else None
        selected_row = None
        self.listing_list.clear()
        sort_mode = self.sort_combo.currentText()
        qualifies_only = self.filter_qualifies.isChecked()
        cfg = self.finance_cfg
        profile = self._active_profile

        # First pass: collect filtered listings with their metadata
        filtered: list[tuple[Listing, Optional[Snapshot], str, float, float]] = []

        for listing in self._listings_cache:
            snap = self._listing_snapshots.get(listing.id)

            listing_class = "within"
            mcc = 0.0
            vscore = 0.0
            if profile and snap and snap.list_price:
                mcc = monthly_carrying_cost(
                    snap.list_price, snap.maintenance_fee or 0,
                    snap.taxes_annual or 0, snap.fee_includes or "", cfg
                )
                fpsf = fee_per_sqft(snap.maintenance_fee or 0, snap.sqft_min, snap.sqft_max)
                listing_class = classify_listing(snap.list_price, mcc, fpsf, profile)
                vm = compute_value_metrics(
                    snap.list_price, snap.maintenance_fee, snap.taxes_annual,
                    snap.fee_includes or "", snap.sqft_min, snap.sqft_max,
                    snap.beds, cfg,
                )
                vscore = vm.score or 0.0

            if qualifies_only and snap and snap.list_price:
                if not qualifies(
                    snap.list_price, snap.maintenance_fee or 0,
                    snap.taxes_annual or 0, snap.fee_includes or "", cfg
                ):
                    continue

            if self.filter_pos.isChecked():
                if not (snap and snap.power_of_sale):
                    continue

            if profile and profile.show_buffer_listings and listing_class == "over":
                continue

            filtered.append((listing, snap, listing_class, mcc, vscore))

        # Sort key based on dropdown selection
        def _sort_key(item: tuple) -> tuple:
            _listing, _snap, _lc, _mcc, _vs = item
            if sort_mode.startswith("Price"):
                val = _snap.list_price if _snap and _snap.list_price else 0
            elif sort_mode.startswith("Sqft"):
                val = _snap.sqft_min if _snap and _snap.sqft_min else 0
            elif sort_mode.startswith("Monthly"):
                val = _mcc
            elif sort_mode.startswith("Value Score"):
                val = _vs
            else:
                return (0,)
            has_val = 0 if val else 1
            if "high-low" in sort_mode:
                return (has_val, -val)
            return (has_val, val)

        if sort_mode != "Default":
            filtered.sort(key=_sort_key)

        # Group by city, ordered by profile priority then alphabetical
        city_groups: dict[str, list[tuple[Listing, Optional[Snapshot], str, float, float]]] = {}
        for listing, snap, lc, mcc, vs in filtered:
            city = listing.city.strip().title() if listing.city else "Unknown"
            city_groups.setdefault(city, []).append((listing, snap, lc, mcc, vs))

        # Sort cities: profile cities first in order, then alphabetical, "Unknown" last
        profile_cities = [c.strip().title() for c in profile.cities] if profile else []
        def city_sort_key(city_name: str) -> tuple[int, int, str]:
            if city_name == "Unknown":
                return (2, 0, "")
            try:
                idx = profile_cities.index(city_name)
                return (0, idx, "")
            except ValueError:
                return (1, 0, city_name)
        sorted_cities = sorted(city_groups.keys(), key=city_sort_key)

        count = 0
        first_listing_row = None
        for city in sorted_cities:
            group = city_groups[city]

            # City header
            header = QListWidgetItem()
            n = len(group)
            header.setText(f"{city}  ({n})")
            header.setData(Qt.ItemDataRole.UserRole, None)
            header.setFlags(Qt.ItemFlag.NoItemFlags)
            header.setSizeHint(QSize(280, 26))
            header.setBackground(QColor("#1a1a2e"))
            header.setForeground(QColor("#81d4fa"))
            font = header.font()
            font.setBold(True)
            font.setPointSize(10)
            header.setFont(font)
            self.listing_list.addItem(header)

            for listing, snap, listing_class, _mcc, _vscore in group:
                item = QListWidgetItem()
                item.setData(Qt.ItemDataRole.UserRole, listing.id)

                if listing.address:
                    addr = listing.address
                else:
                    from urllib.parse import urlparse
                    parsed = urlparse(listing.url)
                    slug = parsed.path.rstrip("/").rsplit("/", 1)[-1] or parsed.netloc
                    addr = f"{parsed.netloc}/{slug}"[:40]
                if listing.unit:
                    addr = f"{listing.unit} — {addr}"

                line1 = addr
                line2_parts = []
                if snap:
                    status_str = snap.status.value if isinstance(snap.status, Status) else "?"
                    if snap.list_price:
                        line2_parts.append(f"${snap.list_price:,.0f}")
                    line2_parts.append(status_str.replace("_", " "))
                    if snap.beds:
                        line2_parts.append(f"{snap.beds}bd")
                    if snap.sqft_min:
                        line2_parts.append(f"{snap.sqft_min}sf")
                else:
                    line2_parts.append("Not yet fetched")

                # Property type tag + colored strip
                ptype = _infer_property_type(listing, snap)
                if ptype:
                    line2_parts.append(ptype)
                item.setData(PTYPE_ROLE, _ptype_color(ptype))

                if listing_class == "buffer":
                    line2_parts.append("WATCHING")

                if snap and snap.power_of_sale:
                    line2_parts.append("POS")

                if _vscore > 0:
                    line2_parts.append(f"V:{_vscore:.0f}")

                if self._cross_ref_results:
                    xref = self._cross_ref_index.get(listing.id)
                    if xref:
                        line2_parts.append(f"XREF({len(xref.listings)})")

                line2 = "  ·  ".join(line2_parts)
                item.setText(f"{line1}\n{line2}")
                item.setSizeHint(QSize(280, 52))

                if listing_class == "buffer":
                    item.setForeground(QColor("#ffa726"))
                elif snap:
                    s = snap.status.value if isinstance(snap.status, Status) else ""
                    if s == "SOLD" or s == "SOLD_CONDITIONAL":
                        item.setForeground(QColor("#ef5350"))
                    elif s == "BLOCKED":
                        item.setForeground(QColor("#ffa726"))
                    elif s != "FOR_SALE":
                        item.setForeground(QColor("#888"))

                self.listing_list.addItem(item)
                if first_listing_row is None:
                    first_listing_row = self.listing_list.count() - 1
                if listing.id == selected_id:
                    selected_row = self.listing_list.count() - 1
                count += 1

        self.listing_count_label.setText(f"{count} listing{'s' if count != 1 else ''}")

        # Keep the listing the user was on; fall back to the top one if it was filtered out
        target_row = selected_row if selected_row is not None else first_listing_row
        if target_row is not None:
            self.listing_list.setCurrentRow(target_row)

    def _on_listing_selected(self, row: int):
        if row < 0:
            return
        item = self.listing_list.item(row)
        if not item:
            return
        listing_id = item.data(Qt.ItemDataRole.UserRole)
        if listing_id is None:
            return

        for listing in self._listings_cache:
            if listing.id == listing_id:
                snapshots = get_snapshots_for_listing(listing_id)
                xref = self._cross_ref_index.get(listing_id)
                self.detail_pane.set_cross_ref(xref, update=False)
                self.detail_pane.show_listing(listing, snapshots)
                break

    def _listing_context_menu(self, pos):
        item = self.listing_list.itemAt(pos)
        if not item:
            return
        listing_id = item.data(Qt.ItemDataRole.UserRole)
        if listing_id is None:
            return

        menu = QMenu(self)
        remove_action = menu.addAction("Remove Listing")
        open_action = menu.addAction("Open in Browser")
        action = menu.exec(self.listing_list.mapToGlobal(pos))

        if action == remove_action:
            listing = next((l for l in self._listings_cache if l.id == listing_id), None)
            addr = listing.address if listing else "this listing"
            reply = QMessageBox.question(
                self, "Remove Listing",
                f"Remove \"{addr}\" and all its snapshots?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Yes:
                delete_listing(listing_id)
                self._listings_cache = [l for l in self._listings_cache if l.id != listing_id]
                self._listing_snapshots.pop(listing_id, None)
                self._populate_list()
                self.statusBar().showMessage(f"Removed: {addr}", 3000)
        elif action == open_action:
            listing = next((l for l in self._listings_cache if l.id == listing_id), None)
            if listing:
                webbrowser.open(listing.url)

    def _apply_filters(self):
        current_id = None
        item = self.listing_list.currentItem()
        if item:
            current_id = item.data(Qt.ItemDataRole.UserRole)

        self._populate_list()

        if current_id is not None:
            for i in range(self.listing_list.count()):
                it = self.listing_list.item(i)
                if it and it.data(Qt.ItemDataRole.UserRole) == current_id:
                    self.listing_list.setCurrentRow(i)
                    return

    # --- Fetch ---

    def _cancel_operation(self):
        """Cancel the current search or fetch operation."""
        cancelled = False
        if hasattr(self, '_search_worker') and self._search_thread and self._search_thread.isRunning():
            self._search_worker.cancel()
            cancelled = True
        if hasattr(self, '_worker') and self._fetch_thread and self._fetch_thread.isRunning():
            self._worker.cancel()
            cancelled = True
        if cancelled:
            self.statusBar().showMessage("Cancelling...", 3000)

    def _start_elapsed(self):
        self._op_start_time = time.monotonic()
        self._elapsed_timer.start()

    def _format_elapsed(self) -> str:
        secs = int(time.monotonic() - self._op_start_time)
        mins, secs = divmod(secs, 60)
        return f"{mins}:{secs:02d}"

    def _update_elapsed(self):
        if self._op_progress_text:
            self.progress_label.setText(
                f"{self._op_progress_text}  [{self._format_elapsed()}]"
            )

    def _reset_ui_after_operation(self):
        """Safety net: re-enable all controls when a thread finishes, regardless of how."""
        self._elapsed_timer.stop()
        self.recheck_btn.setEnabled(True)
        self.search_btn.setEnabled(True)
        self.cancel_btn.setVisible(False)
        self.progress_bar.setVisible(False)
        self.progress_label.setVisible(False)

    def _recheck_all(self):
        if self._fetch_thread and self._fetch_thread.isRunning():
            QMessageBox.information(self, "Busy", "A fetch is already in progress.")
            return

        listings = get_all_listings(active_only=True)
        if not listings:
            QMessageBox.information(self, "Nothing to check", "No active listings to fetch.")
            return

        self.recheck_btn.setEnabled(False)
        self.cancel_btn.setVisible(True)
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, len(listings))
        self.progress_bar.setValue(0)
        self._start_elapsed()

        self._fetch_thread = QThread()
        self._worker = FetchWorker(listings, self.show_browser_cb.isChecked())
        self._worker.moveToThread(self._fetch_thread)

        self._fetch_thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_fetch_progress)
        self._worker.finished.connect(self._on_fetch_finished)
        self._worker.error.connect(self._on_fetch_error)
        self._worker.finished.connect(self._fetch_thread.quit)
        self._worker.error.connect(self._fetch_thread.quit)
        self._fetch_thread.finished.connect(self._reset_ui_after_operation)

        self._fetch_thread.start()

    def _on_fetch_progress(self, current: int, total: int, address: str):
        if total != self.progress_bar.maximum():
            self.progress_bar.setMaximum(max(total, 1))
        self.progress_bar.setValue(current + 1)
        self._op_progress_text = f"Fetching {current+1}/{total}: {address}"
        self.progress_label.setText(f"{self._op_progress_text}  [{self._format_elapsed()}]")
        self.progress_label.setVisible(True)
        self.statusBar().showMessage(f"Fetching {current+1}/{total}: {address}")

    def _on_fetch_finished(self, snapshots: list, changes: list):
        elapsed = self._format_elapsed()
        self._elapsed_timer.stop()
        self.recheck_btn.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.progress_label.setVisible(False)
        self.last_refresh_label.setText(
            f"Last refresh: {datetime.now().strftime('%H:%M:%S')}"
        )

        removed = cleanup_bad_listings(after_fetch=True)
        sold_removed = cleanup_non_purchasable()
        total_removed = removed + sold_removed
        msg = f"Done — {len(snapshots)} fetched, {len(changes)} changes  [{elapsed}]"
        if total_removed:
            msg += f", {total_removed} non-purchasable removed"
        self.statusBar().showMessage(msg, 15000)

        self._show_changes(changes)
        self._refresh_list_no_cleanup()
        self._refresh_cross_refs()
        self._send_notifications(changes)

    def _on_fetch_error(self, error: str):
        self.recheck_btn.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.progress_label.setVisible(False)
        QMessageBox.warning(self, "Fetch Error", f"Error during fetch:\n{error}")

    def _show_changes(self, changes: list[Change]):
        if not changes:
            self.changes_frame.setVisible(False)
            return

        self.changes_frame.setVisible(True)
        lines = []
        for c in changes:
            addr = c.listing.address or c.listing.url
            if c.listing.unit:
                addr = f"{c.listing.unit} — {addr}"

            if c.severity == "critical":
                prefix = '<span style="color:#ef5350; font-weight:bold;">●</span>'
            elif c.severity == "warning":
                prefix = '<span style="color:#ffa726; font-weight:bold;">●</span>'
            else:
                prefix = '<span style="color:#666;">●</span>'

            lines.append(f"{prefix} {addr}: {c.description}")

        self.changes_content.setText("<br>".join(lines))
        self.changes_content.setTextFormat(Qt.TextFormat.RichText)

    def _dismiss_changes(self):
        self.changes_frame.setVisible(False)

    def _send_notifications(self, changes: list[Change]):
        critical = [c for c in changes if c.field == "status"]
        if not critical:
            return
        try:
            from plyer import notification

            for c in critical[:5]:
                addr = c.listing.address or c.listing.url
                notification.notify(
                    title="Listing Status Change",
                    message=f"{addr}: {c.description}",
                    app_name="Durham Listing Tracker",
                    timeout=10,
                )
        except ImportError:
            logger.info("plyer not installed — skipping desktop notifications")
        except Exception as e:
            logger.warning("Notification failed: %s", e)

    # --- Add listing ---

    def _add_listing(self):
        url, ok = QInputDialog.getText(self, "Add Listing", "Paste listing URL:")
        if not ok or not url.strip():
            return
        url = url.strip()

        existing = get_listing_by_url(url)
        if existing:
            QMessageBox.information(self, "Exists", "This listing is already tracked.")
            return

        adapter = get_adapter_for_url(url)
        if adapter is None:
            QMessageBox.warning(
                self, "Unsupported Site",
                "No adapter for this URL. Supported sites:\n"
                "strata.ca, condos.ca, realtor.ca, housesigma.com, zolo.ca,\n"
                "wahi.com, rew.ca, condonow.com, condominiums.ca"
            )
            return

        add_listing(url, adapter.site_name)
        logger.info("Added listing: %s", url)
        self._load_listings()

    # --- Auto-search ---

    def _auto_search(self):
        from listingtracker.ui.site_picker import SitePickerDialog
        from listingtracker.profiles import save_profile

        if not self._active_profile:
            QMessageBox.information(
                self, "No Profile",
                "Create and select a buyer profile first.\n"
                "The auto-search uses your profile's cities and budget to find listings."
            )
            return

        if self._search_thread and self._search_thread.isRunning():
            QMessageBox.information(self, "Busy", "A search is already in progress.")
            return

        dialog = SitePickerDialog(self._active_profile.search_sites, self)
        if dialog.exec() != SitePickerDialog.DialogCode.Accepted:
            return

        selected = dialog.selected_sites()
        if not selected:
            QMessageBox.information(self, "No Sites", "Select at least one site to search.")
            return

        if selected != self._active_profile.search_sites:
            self._active_profile.search_sites = selected
            save_profile(self._active_profile)

        self._start_search(selected)

    def _start_search(self, selected: list[str]):
        urls = build_search_urls(self._active_profile, sites=selected)
        if not urls:
            QMessageBox.information(self, "No Search URLs", "No search URLs could be built from the profile.")
            return

        self.search_btn.setEnabled(False)
        self.recheck_btn.setEnabled(False)
        self.cancel_btn.setVisible(True)
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, len(urls))
        self.progress_bar.setValue(0)
        self._start_elapsed()

        self._search_thread = QThread()
        self._search_worker = SearchWorker(urls, self.show_browser_cb.isChecked())
        self._search_worker.moveToThread(self._search_thread)

        self._search_thread.started.connect(self._search_worker.run)
        self._search_worker.progress.connect(self._on_search_progress)
        self._search_worker.finished.connect(self._on_search_finished)
        self._search_worker.error.connect(self._on_search_error)
        self._search_worker.finished.connect(self._search_thread.quit)
        self._search_worker.error.connect(self._search_thread.quit)
        self._search_thread.finished.connect(self._reset_ui_after_operation)

        self._search_thread.start()

    def _on_search_progress(self, current: int, total: int, description: str):
        if total != self.progress_bar.maximum():
            self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(current + 1)
        self._op_progress_text = f"Scanning {current+1}/{total}: {description}"
        self.progress_label.setText(f"{self._op_progress_text}  [{self._format_elapsed()}]")
        self.progress_label.setVisible(True)
        self.statusBar().showMessage(f"Searching {current+1}/{total}: {description}")

    def _on_search_finished(self, discovered_urls: list):
        # Don't re-enable buttons yet — fetch phase follows

        # Normalize all discovered URLs and deduplicate
        normalized = list(dict.fromkeys(normalize_url(u) for u in discovered_urls))
        existing_urls = {listing.url for listing in get_all_listings(active_only=False)}
        new_urls = [u for u in normalized if u not in existing_urls]

        new_listings = []
        skipped = 0
        for url in new_urls:
            adapter = get_adapter_for_url(url)
            if adapter is None or not adapter.is_listing_url(url):
                skipped += 1
                continue
            listing = add_listing(url, adapter.site_name)
            if listing is not None:
                new_listings.append(listing)

        self._refresh_list_no_cleanup()

        already = len(normalized) - len(new_urls)
        added = len(new_listings)

        if added > 0:
            elapsed = self._format_elapsed()
            self.statusBar().showMessage(
                f"Found {added} new listings in {elapsed} — now fetching details...", 10000
            )
            self._fetch_new_listings(new_listings, from_auto_search=True)
        else:
            elapsed = self._format_elapsed()
            self._elapsed_timer.stop()
            self.recheck_btn.setEnabled(True)
            self.search_btn.setEnabled(True)
            self.cancel_btn.setVisible(False)
            self.progress_bar.setVisible(False)
            self.progress_label.setVisible(False)
            self.statusBar().showMessage(
                f"Search done — no new listings ({already} already tracked)  [{elapsed}]", 15000
            )

    def _fetch_new_listings(self, listings, from_auto_search: bool = False):
        if self._fetch_thread and self._fetch_thread.isRunning():
            self.statusBar().showMessage(
                f"Added {len(listings)} listings — click Recheck All when ready", 8000
            )
            self.progress_bar.setVisible(False)
            self.progress_label.setVisible(False)
            return

        self.recheck_btn.setEnabled(False)
        self.search_btn.setEnabled(False)
        self.cancel_btn.setVisible(True)
        self.progress_bar.setRange(0, len(listings))
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(True)
        self._op_progress_text = f"Fetching details for {len(listings)} new listings..."
        self.progress_label.setText(self._op_progress_text)
        self.progress_label.setVisible(True)
        if not from_auto_search:
            self._start_elapsed()

        self._fetch_thread = QThread()
        self._worker = FetchWorker(listings, self.show_browser_cb.isChecked())
        self._worker.moveToThread(self._fetch_thread)

        self._fetch_thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_fetch_progress)
        self._worker.finished.connect(self._on_fetch_finished)
        self._worker.error.connect(self._on_fetch_error)
        self._worker.finished.connect(self._fetch_thread.quit)
        self._worker.error.connect(self._fetch_thread.quit)
        self._fetch_thread.finished.connect(self._reset_ui_after_operation)

        self._fetch_thread.start()

    def _on_search_error(self, error: str):
        self.search_btn.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.progress_label.setVisible(False)
        QMessageBox.warning(self, "Search Error", f"Error during auto-search:\n{error}")

    # --- Settings ---

    def _open_settings(self):
        dialog = SettingsDialog(self.config, self)
        if dialog.exec():
            self.config = dialog.get_config()
            save_config(self.config)
            self.finance_cfg = config_to_finance(self.config)
            self.detail_pane.set_finance_config(self.finance_cfg)
            self._load_listings()

    # --- Export ---

    def _export_csv(self):
        EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
        filename = EXPORTS_DIR / f"listings-{datetime.now().strftime('%Y%m%d-%H%M%S')}.csv"

        headers = [
            "Address", "Unit", "Building", "City", "Status", "Price",
            "Maint Fee", "Taxes", "Beds", "Baths", "Sqft", "$/sqft",
            "Fee/sqft", "Monthly All-in", "Qualifies", "Classification",
            "DOM", "MLS#", "Power of Sale",
        ]

        with open(filename, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(headers)
            cfg = self.finance_cfg
            profile = self._active_profile
            for listing in self._listings_cache:
                snap = self._listing_snapshots.get(listing.id)
                if not snap:
                    continue
                fee_includes = snap.fee_includes or ""
                ppsf = price_per_sqft(snap.list_price or 0, snap.sqft_min, snap.sqft_max)
                fpsf = fee_per_sqft(snap.maintenance_fee or 0, snap.sqft_min, snap.sqft_max)
                mcc = monthly_carrying_cost(
                    snap.list_price or 0, snap.maintenance_fee or 0,
                    snap.taxes_annual or 0, fee_includes, cfg
                ) if snap.list_price else ""
                q = qualifies(
                    snap.list_price or 0, snap.maintenance_fee or 0,
                    snap.taxes_annual or 0, fee_includes, cfg
                ) if snap.list_price else ""

                classification = ""
                if profile and snap.list_price:
                    classification = classify_listing(
                        snap.list_price,
                        mcc if isinstance(mcc, float) else None,
                        fpsf, profile
                    )

                sqft = ""
                if snap.sqft_min and snap.sqft_max and snap.sqft_min != snap.sqft_max:
                    sqft = f"{snap.sqft_min}-{snap.sqft_max}"
                elif snap.sqft_min:
                    sqft = str(snap.sqft_min)

                status = snap.status.value if isinstance(snap.status, Status) else str(snap.status)

                writer.writerow([
                    listing.address, listing.unit or "", listing.building_name or "",
                    listing.city, status,
                    f"{snap.list_price:,.0f}" if snap.list_price else "",
                    f"{snap.maintenance_fee:,.0f}" if snap.maintenance_fee else "",
                    f"{snap.taxes_annual:,.0f}" if snap.taxes_annual else "",
                    snap.beds or "", snap.baths or "", sqft,
                    f"{ppsf:,.0f}" if ppsf else "",
                    f"{fpsf:,.2f}" if fpsf else "",
                    f"{mcc:,.0f}" if isinstance(mcc, float) else "",
                    "YES" if q is True else ("NO" if q is False else ""),
                    classification,
                    snap.days_on_market or "", listing.mls_number or "",
                    "YES" if snap.power_of_sale else "",
                ])

        self.statusBar().showMessage(f"Exported to {filename}", 5000)

    # --- Research: other sites + price history + local news ---

    def _research_listing(self, listing_id: int):
        listing = next((l for l in self._listings_cache if l.id == listing_id), None)
        if not listing:
            return
        browser_busy = any(t and t.isRunning() for t in (self._enrich_thread, self._history_thread))
        if self._research_pending or browser_busy:
            self.statusBar().showMessage("Research already running, please wait", 3000)
            return

        self._research_listing_id = listing_id
        self._research_pending = {"news", "sites", "history"}
        self.detail_pane.set_research_running(True)

        self.detail_pane.set_area_news(listing_id, None, searching=True)
        threading.Thread(target=self._area_news_job, args=(listing,), daemon=True).start()

        # The site search and the history fetch each drive a browser, so they run one after the other
        if not self._run_cross_ref_single(listing_id):
            self._research_step_done("sites")

    def _area_news_job(self, listing: Listing):
        try:
            news = areanews.fetch_area_news(listing.address, listing.city, listing.neighbourhood)
        except Exception as e:
            logger.error("Area news lookup failed: %s", e, exc_info=True)
            news = None
        try:
            self._area_news_ready.emit(listing.id, news)
        except RuntimeError:
            pass  # window closed while the lookup ran

    def _on_area_news(self, listing_id: int, news):
        if news is None:
            news = areanews.AreaNews(failed=True)
        self.detail_pane.set_area_news(listing_id, news, searching=False)
        self._research_step_done("news")

    def _research_step_done(self, step: str):
        if step not in self._research_pending:
            return
        self._research_pending.discard(step)
        if step == "sites" and not self._fetch_listing_history(self._research_listing_id):
            self._research_pending.discard("history")
        if not self._research_pending:
            self.detail_pane.set_research_running(False)
            self.statusBar().showMessage("Research complete", 8000)

    # --- Cross-reference ---

    def _refresh_cross_refs(self):
        """Silently update cross-ref results after data changes."""
        if self._listings_cache:
            self._cross_ref_results = build_cross_ref_results(
                self._listings_cache, self._listing_snapshots
            )
        else:
            self._cross_ref_results = []
        self._cross_ref_index = {}
        for xref in self._cross_ref_results:
            for l in xref.listings:
                self._cross_ref_index[l.id] = xref

    def _run_cross_ref_single(self, listing_id: int) -> bool:
        """Cross-check a single listing: instant merge + launch browser enrichment.

        Returns True when a background site search was started.
        """
        if not self._listings_cache:
            return False

        listing = next((l for l in self._listings_cache if l.id == listing_id), None)
        if not listing:
            return False

        snap = self._listing_snapshots.get(listing_id)

        try:
            # Phase 1: Instant cross-ref merge
            results = build_cross_ref_results(self._listings_cache, self._listing_snapshots)
            self._cross_ref_results = results
            self._cross_ref_index = {}
            for xr in results:
                for l in xr.listings:
                    self._cross_ref_index[l.id] = xr
            self._populate_list()

            xref = self._cross_ref_index.get(listing_id)
            self.detail_pane.set_cross_ref(xref, update=False)

            instant_items = merge_from_cross_ref(listing_id, snap, xref)

            # Show instant results + "searching..." indicator (triggers single display update)
            enrichment = EnrichmentResult(listing_id=listing_id, items=list(instant_items))
            self.detail_pane.set_enrichment(enrichment, searching=True)

            if xref:
                other_count = len(xref.listings) - 1
                self.statusBar().showMessage(
                    f"Cross-check: {other_count} match{'es' if other_count != 1 else ''} found, searching other sites...", 5000
                )
            else:
                self.statusBar().showMessage("Cross-check: searching other sites...", 5000)
        except Exception as e:
            logger.error("Cross-check phase 1 failed: %s", e, exc_info=True)
            self.statusBar().showMessage(f"Cross-check error: {e}", 5000)
            return False

        # Phase 2: Browser-based enrichment
        search_urls = build_enrichment_search_urls(listing)
        if not search_urls:
            enrichment = EnrichmentResult(listing_id=listing_id, items=list(instant_items))
            self.detail_pane.set_enrichment(enrichment, searching=False)
            self.statusBar().showMessage("Cross-check complete — no additional sites to search", 5000)
            return False

        if self._enrich_thread and self._enrich_thread.isRunning():
            self.statusBar().showMessage("Enrichment already running, please wait", 3000)
            return False

        if self._history_thread and self._history_thread.isRunning():
            self.statusBar().showMessage("History fetch running — wait before cross-checking", 3000)
            return False

        show_browser = self.show_browser_cb.isChecked()
        worker = EnrichWorker(listing, snap, search_urls, show_browser)
        thread = QThread()
        worker.moveToThread(thread)

        # Must be a method of this window, not a lambda: a lambda would run on the worker's
        # thread, and touching the status bar from there crashes Qt a few seconds later.
        worker.progress.connect(self._on_enrich_progress)
        worker.finished.connect(self._on_enrich_finished)
        worker.error.connect(self._on_enrich_error)

        thread.started.connect(worker.run)
        worker.finished.connect(thread.quit)
        worker.error.connect(thread.quit)

        self._enrich_thread = thread
        self._enrich_worker = worker
        thread.start()
        return True

    def _on_enrich_progress(self, description: str):
        self.statusBar().showMessage(f"Enriching: {description}")

    def _on_enrich_finished(self, listing_id: int, site_results: list):
        """Handle enrichment results from browser scraping."""
        snap = self._listing_snapshots.get(listing_id)
        xref = self._cross_ref_index.get(listing_id)

        # Combine instant merge items with browser results
        instant_items = merge_from_cross_ref(listing_id, snap, xref)
        browser_items = []
        sites_searched = []
        sites_found = []

        listing = next((l for l in self._listings_cache or [] if l.id == listing_id), None)
        for sr in site_results:
            sites_searched.append(sr["site"])
            if listing is None or not is_same_property(listing, sr["parsed"]):
                logger.info("Ignoring %s result: not confirmed as the same property", sr["site"])
                continue
            items = build_enrichment_from_parsed(
                listing_id, snap, sr["site"], sr["url"], sr["parsed"]
            )
            if items:
                sites_found.append(sr["site"])
                browser_items.extend(items)

        # Merge: prefer browser items (fresher), deduplicate by field
        all_items = list(instant_items)
        seen_fields = {item.field for item in all_items}
        for item in browser_items:
            if item.field not in seen_fields:
                all_items.append(item)
                seen_fields.add(item.field)

        enrichment = EnrichmentResult(
            listing_id=listing_id,
            items=all_items,
            sites_searched=sites_searched,
            sites_found=sites_found,
        )
        if self.detail_pane.current_listing_id() == listing_id:
            self.detail_pane.set_enrichment(enrichment, searching=False)

        found_count = len(all_items)
        searched_count = len(sites_searched)
        self.statusBar().showMessage(
            f"Cross-check complete: found {found_count} field{'s' if found_count != 1 else ''} "
            f"across {searched_count} site{'s' if searched_count != 1 else ''}",
            8000,
        )
        self._research_step_done("sites")

    def _on_enrich_error(self, error_msg: str):
        self.statusBar().showMessage(f"Cross-check error: {error_msg}", 5000)
        listing_id = getattr(self, '_enrich_worker', None)
        if listing_id and hasattr(listing_id, 'listing'):
            lid = listing_id.listing.id
            snap = self._listing_snapshots.get(lid)
            xref = self._cross_ref_index.get(lid)
            instant = merge_from_cross_ref(lid, snap, xref)
            enrichment = EnrichmentResult(listing_id=lid, items=list(instant))
            if self.detail_pane.current_listing_id() == lid:
                self.detail_pane.set_enrichment(enrichment, searching=False)
        self._research_step_done("sites")

    def _apply_enrichment(self, listing_id: int, fields: dict):
        """Apply enrichment data to a listing's latest snapshot."""
        from listingtracker.db import update_snapshot_fields, get_latest_snapshot as get_snap
        snap = get_snap(listing_id)
        if not snap or not snap.id:
            return

        update_snapshot_fields(snap.id, fields)

        for field_name, value in fields.items():
            setattr(snap, field_name, value)

        self._listing_snapshots[listing_id] = snap

        listing = next((l for l in self._listings_cache if l.id == listing_id), None)
        if listing:
            self.detail_pane.show_listing(listing, [snap])

        self.statusBar().showMessage(
            f"Applied {len(fields)} field{'s' if len(fields) != 1 else ''} from cross-check", 5000
        )

    # --- On-demand history fetch ---

    def _fetch_listing_history(self, listing_id: int) -> bool:
        """Fetch fresh data for a single listing to build price history. Returns True if started."""
        listing = next((l for l in self._listings_cache if l.id == listing_id), None)
        if not listing:
            self.detail_pane.set_history_fetch_done()
            return False

        if self._history_thread and self._history_thread.isRunning():
            self.statusBar().showMessage("History fetch already running, please wait", 3000)
            self.detail_pane.set_history_fetch_done()
            return False

        self.statusBar().showMessage(f"Fetching history for {listing.address or listing.url}...")

        worker = HistoryFetchWorker(listing, self.show_browser_cb.isChecked())
        thread = QThread()
        worker.moveToThread(thread)

        worker.finished.connect(self._on_history_finished)
        worker.error.connect(self._on_history_error)

        thread.started.connect(worker.run)
        worker.finished.connect(thread.quit)
        worker.error.connect(thread.quit)

        self._history_thread = thread
        self._history_worker = worker
        thread.start()
        return True

    def _on_history_finished(self, listing_id: int, snap_count: int):
        listing = next((l for l in self._listings_cache if l.id == listing_id), None)
        found_new = snap_count >= 2
        if listing:
            from listingtracker.db import get_snapshots_for_listing
            all_snaps = get_snapshots_for_listing(listing_id)
            if all_snaps:
                found_new = len(all_snaps) >= 2
                self._listing_snapshots[listing_id] = all_snaps[-1]
                if self.detail_pane.current_listing_id() == listing_id:
                    self.detail_pane.show_listing(listing, all_snaps)
            self._populate_list()
        self.detail_pane.set_history_fetch_done(found_new=found_new)
        self.statusBar().showMessage(
            f"History fetch complete — {snap_count} total snapshot{'s' if snap_count != 1 else ''}", 5000
        )
        self._research_step_done("history")

    def _on_history_error(self, error_msg: str):
        self.detail_pane.set_history_fetch_done()
        self.statusBar().showMessage(f"History fetch error: {error_msg}", 5000)
        self._research_step_done("history")

    # --- Notes & browser ---

    def _on_notes_saved(self, listing_id: int, text: str):
        for listing in self._listings_cache:
            if listing.id == listing_id:
                listing.user_notes = text
                update_listing(listing)
                self.statusBar().showMessage("Notes saved", 3000)
                break

    def _open_url_in_browser(self, url: str):
        webbrowser.open(url)

    def closeEvent(self, event):
        """Clean up worker threads on window close."""
        for thread_attr, worker_attr in [
            ("_fetch_thread", "_worker"),
            ("_search_thread", "_search_worker"),
            ("_enrich_thread", "_enrich_worker"),
            ("_history_thread", "_history_worker"),
        ]:
            thread = getattr(self, thread_attr, None)
            worker = getattr(self, worker_attr, None)
            if thread and thread.isRunning():
                if worker and hasattr(worker, "cancel"):
                    worker.cancel()
                thread.quit()
                thread.wait(5000)
        super().closeEvent(event)
