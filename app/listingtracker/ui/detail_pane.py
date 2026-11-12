"""Detail pane — tabbed layout with Highlights, Utilities/Budget, Financials with sliders."""

from __future__ import annotations

import logging
from datetime import date, timedelta
from html import escape
from typing import Optional

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QTextEdit,
    QPushButton, QScrollArea, QFrame, QGridLayout, QGroupBox,
    QTabWidget, QSlider, QSpinBox,
)
from PySide6.QtCore import Qt, Signal

from listingtracker.models import Listing, Snapshot, Status
from listingtracker.finance import (
    FinanceConfig, monthly_carrying_cost, max_qualifying_price,
    qualifies, price_per_sqft, fee_per_sqft, cash_left_after_close,
    ontario_land_transfer_tax, first_time_buyer_rebate, mortgage_breakdown,
    down_payment_for_budget, price_target_for_budget, _calc_dp_scenario,
    down_payment_required,
)
from listingtracker.crossref import CrossRefResult, EnrichmentResult, EnrichmentItem
from listingtracker.search import check_deal_breakers
from listingtracker.valuescore import compute_value_metrics, ValueMetrics
from listingtracker.areanews import AreaNews, CATEGORY_LABELS, SCOPES
from listingtracker.profiles import BuyerProfile
from listingtracker.ui.commute_tab import CommuteTab
from listingtracker import db as listing_db

logger = logging.getLogger(__name__)

CARD_STYLE = """
QGroupBox {
    background: #1e1e1e;
    border: 1px solid #333;
    border-radius: 8px;
    margin-top: 12px;
    padding: 12px 10px 10px 10px;
    font-weight: bold;
    font-size: 13px;
    color: #ccc;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 14px;
    padding: 0 6px;
    color: #81d4fa;
}
"""

TAB_STYLE = """
QTabWidget::pane {
    border: 1px solid #333;
    border-radius: 6px;
    background: #1e1e1e;
    padding: 8px;
}
QTabBar::tab {
    background: #2a2a3e;
    color: #aaa;
    border: 1px solid #333;
    border-bottom: none;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    padding: 6px 16px;
    margin-right: 2px;
    font-size: 12px;
}
QTabBar::tab:selected {
    background: #1e1e1e;
    color: #81d4fa;
    font-weight: bold;
}
QTabBar::tab:hover:!selected {
    background: #333;
    color: #ccc;
}
"""

SLIDER_STYLE = """
QSlider::groove:horizontal {
    border: 1px solid #444;
    height: 6px;
    background: #2a2a3e;
    border-radius: 3px;
}
QSlider::handle:horizontal {
    background: #81d4fa;
    border: 1px solid #5bb;
    width: 16px;
    margin: -5px 0;
    border-radius: 8px;
}
QSlider::sub-page:horizontal {
    background: #3a6a8a;
    border-radius: 3px;
}
"""

VAL_LABEL = "color: #e0e0e0; font-size: 13px;"
KEY_LABEL = "color: #888; font-size: 12px;"
BIG_NUM = "color: #fff; font-size: 22px; font-weight: bold;"
QUALIFY_YES = "color: #66bb6a; font-size: 15px; font-weight: bold;"
QUALIFY_NO = "color: #ef5350; font-size: 15px; font-weight: bold;"

AREA_NEWS_COLORS = {
    "violent": "#ef5350",
    "theft": "#ffa726",
    "fire": "#ff8a65",
    "death": "#b0bec5",
    "disruption": "#81d4fa",
}

PTYPE_HEADER_COLORS = {
    "CONDO": "#1a2e24",
    "TOWN": "#1a2434",
    "FREE": "#1a2434",
    "SEMI": "#1a2434",
    "RENTAL": "#2e1a1a",
}

def _header_bg_for_ptype(snap_property_type: Optional[str], url: str = "",
                          maintenance_fee: Optional[float] = None) -> str:
    if snap_property_type:
        pt = snap_property_type.lower()
        if "condo" in pt or "apartment" in pt:
            return PTYPE_HEADER_COLORS["CONDO"]
        if "town" in pt:
            return PTYPE_HEADER_COLORS["TOWN"]
        if "semi" in pt:
            return PTYPE_HEADER_COLORS["SEMI"]
        if "detach" in pt:
            return PTYPE_HEADER_COLORS["FREE"]
    url_lower = url.lower()
    if "rent" in url_lower or "lease" in url_lower:
        return PTYPE_HEADER_COLORS["RENTAL"]
    if "condo" in url_lower or "apartment" in url_lower:
        return PTYPE_HEADER_COLORS["CONDO"]
    if maintenance_fee and maintenance_fee > 0:
        return PTYPE_HEADER_COLORS["CONDO"]
    if "townhouse" in url_lower or "townhome" in url_lower:
        return PTYPE_HEADER_COLORS["TOWN"]
    if "house" in url_lower or "detached" in url_lower:
        return PTYPE_HEADER_COLORS["FREE"]
    return "#1a1a2e"


class DetailPane(QWidget):
    notes_saved = Signal(int, str)
    open_in_browser = Signal(str)
    research_requested = Signal(int)
    apply_enrichment_requested = Signal(int, dict)
    fetch_history_requested = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._listing: Optional[Listing] = None
        self._snapshots: list[Snapshot] = []
        self._finance_cfg = FinanceConfig()
        self._cross_ref: Optional[CrossRefResult] = None
        self._enrichment: Optional[EnrichmentResult] = None
        self._enrichment_searching: bool = False
        self._profile: Optional[BuyerProfile] = None
        self._commute_tab: Optional[CommuteTab] = None
        self._area_news: dict[int, AreaNews] = {}
        self._area_news_searching: set[int] = set()
        self._slider_updating = False
        self._build_ui()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")

        self._content = QWidget()
        self._content.setStyleSheet("background: transparent;")
        self._layout = QVBoxLayout(self._content)
        self._layout.setContentsMargins(16, 12, 16, 12)
        self._layout.setSpacing(0)

        # Header container with property-type colored background
        self._header_container = QFrame()
        self._header_container.setStyleSheet(
            "QFrame { background: #1a1a2e; border-radius: 8px; padding: 0; margin: 0 0 6px 0; }"
        )
        hdr_layout = QVBoxLayout(self._header_container)
        hdr_layout.setContentsMargins(14, 10, 14, 10)
        hdr_layout.setSpacing(0)

        self.title_label = QLabel("Select a listing to view details")
        self.title_label.setStyleSheet("font-size: 20px; font-weight: bold; color: #fff; background: transparent;")
        self.title_label.setWordWrap(True)
        hdr_layout.addWidget(self.title_label)

        self.subtitle_label = QLabel()
        self.subtitle_label.setStyleSheet("font-size: 13px; color: #ccc; margin-bottom: 4px; background: transparent;")
        hdr_layout.addWidget(self.subtitle_label)

        self.status_label = QLabel()
        self.status_label.setStyleSheet("font-size: 14px; margin-bottom: 2px; background: transparent;")
        hdr_layout.addWidget(self.status_label)

        self.price_hero = QLabel()
        self.price_hero.setStyleSheet(BIG_NUM + " background: transparent;")
        hdr_layout.addWidget(self.price_hero)

        self.qualify_label = QLabel()
        self.qualify_label.setStyleSheet("background: transparent;")
        hdr_layout.addWidget(self.qualify_label)

        # Property stats row
        self.stats_label = QLabel()
        self.stats_label.setStyleSheet("color: #81d4fa; font-size: 13px; font-weight: bold; padding: 6px 0 2px 0; background: transparent;")
        hdr_layout.addWidget(self.stats_label)

        self.commute_label = QLabel()
        self.commute_label.setStyleSheet("color: #ccc; font-size: 12px; padding: 0 0 2px 0; background: transparent;")
        self.commute_label.setVisible(False)
        hdr_layout.addWidget(self.commute_label)

        self.type_label = QLabel()
        self.type_label.setStyleSheet("color: #ddd; font-size: 11px; padding: 0 0 2px 0; background: transparent;")
        hdr_layout.addWidget(self.type_label)

        self.pos_label = QLabel()
        self.pos_label.setStyleSheet(
            "color: #fff; background: #c62828; font-size: 11px; font-weight: bold; "
            "padding: 2px 8px; border-radius: 3px; margin: 2px 0;"
        )
        self.pos_label.setVisible(False)
        hdr_layout.addWidget(self.pos_label)

        self.broker_label = QLabel()
        self.broker_label.setStyleSheet("color: #aaa; font-size: 11px; padding-top: 2px; background: transparent;")
        self.broker_label.setWordWrap(True)
        hdr_layout.addWidget(self.broker_label)

        self._layout.addWidget(self._header_container)

        # Tabs
        self.tabs = QTabWidget()
        self.tabs.setStyleSheet(TAB_STYLE)
        self._layout.addWidget(self.tabs)

        # Cards area (for non-tab cards like cross-check, enrichment, parse errors, timeline)
        self.cards_widget = QWidget()
        self.cards_layout = QVBoxLayout(self.cards_widget)
        self.cards_layout.setContentsMargins(0, 0, 0, 0)
        self.cards_layout.setSpacing(0)
        self._layout.addWidget(self.cards_widget)

        # Chart area with fetch history button
        self.chart_container = QWidget()
        self.chart_layout = QVBoxLayout(self.chart_container)
        self.chart_layout.setContentsMargins(0, 8, 0, 0)

        chart_header = QHBoxLayout()
        chart_title = QLabel("Price History")
        chart_title.setStyleSheet("color: #81d4fa; font-size: 12px; font-weight: bold;")
        chart_header.addWidget(chart_title)
        chart_header.addStretch()
        self.fetch_history_btn = QPushButton("Fetch History")
        self.fetch_history_btn.setStyleSheet(
            "QPushButton { background: #5c3d99; color: #e0c0ff; border: none; "
            "border-radius: 4px; padding: 4px 12px; font-size: 11px; }"
            "QPushButton:hover { background: #7b52c7; }"
            "QPushButton:disabled { background: #3a3a3a; color: #666; }"
        )
        self.fetch_history_btn.clicked.connect(self._request_fetch_history)
        chart_header.addWidget(self.fetch_history_btn)
        self.chart_layout.addLayout(chart_header)

        self.chart_widget = QLabel("Price history chart appears after 2+ fetches")
        self.chart_widget.setMinimumHeight(180)
        self.chart_widget.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.chart_widget.setStyleSheet(
            "background: #1e1e1e; border: 1px solid #333; border-radius: 8px; "
            "color: #666; font-size: 12px;"
        )
        self.chart_layout.addWidget(self.chart_widget)
        self._layout.addWidget(self.chart_container)

        # Notes
        notes_group = QGroupBox("My Notes")
        notes_group.setStyleSheet(CARD_STYLE)
        notes_inner = QVBoxLayout(notes_group)
        self.notes_edit = QTextEdit()
        self.notes_edit.setMaximumHeight(80)
        self.notes_edit.setStyleSheet(
            "QTextEdit { background: #252525; color: #ddd; border: 1px solid #444; "
            "border-radius: 4px; padding: 4px; font-size: 12px; }"
        )
        notes_inner.addWidget(self.notes_edit)

        btn_row = QHBoxLayout()
        self.save_notes_btn = QPushButton("Save Notes")
        self.save_notes_btn.setStyleSheet(
            "QPushButton { background: #2d5a27; color: #fff; border: none; "
            "border-radius: 4px; padding: 5px 14px; font-size: 12px; }"
            "QPushButton:hover { background: #3a7a30; }"
        )
        self.save_notes_btn.clicked.connect(self._save_notes)
        btn_row.addWidget(self.save_notes_btn)

        self.open_btn = QPushButton("Open in Browser")
        self.open_btn.setStyleSheet(
            "QPushButton { background: #1a5276; color: #fff; border: none; "
            "border-radius: 4px; padding: 5px 14px; font-size: 12px; }"
            "QPushButton:hover { background: #21689a; }"
        )
        self.open_btn.clicked.connect(self._open_browser)
        btn_row.addWidget(self.open_btn)

        self.research_btn = QPushButton("Research More Details")
        self.research_btn.setToolTip(
            "Check other listing sites, fetch price history, and search local news "
            "for crime, fires and other incidents near this home"
        )
        self.research_btn.setStyleSheet(
            "QPushButton { background: #00695c; color: #fff; border: none; "
            "border-radius: 4px; padding: 5px 14px; font-size: 12px; }"
            "QPushButton:hover { background: #00897b; }"
            "QPushButton:disabled { background: #3a3a3a; color: #888; }"
        )
        self.research_btn.clicked.connect(self._request_research)
        btn_row.addWidget(self.research_btn)
        btn_row.addStretch()
        notes_inner.addLayout(btn_row)
        self._layout.addWidget(notes_group)

        self._layout.addStretch()
        scroll.setWidget(self._content)
        outer.addWidget(scroll)

    def _show_commute_summary(self, text: str):
        self.commute_label.setText(text)
        self.commute_label.setVisible(True)

    def set_finance_config(self, cfg: FinanceConfig):
        self._finance_cfg = cfg
        if self._listing and self._snapshots:
            self._update_display()

    def set_profile(self, profile: Optional[BuyerProfile]):
        self._profile = profile
        if self._listing and self._snapshots:
            self._update_display()

    def set_cross_ref(self, xref: Optional[CrossRefResult], update: bool = True):
        self._cross_ref = xref
        if update and self._listing and self._snapshots:
            self._update_display()

    def set_enrichment(self, enrichment: Optional[EnrichmentResult], searching: bool = False):
        self._enrichment = enrichment
        self._enrichment_searching = searching
        if self._listing and self._snapshots:
            self._update_display()

    def show_listing(self, listing: Listing, snapshots: list[Snapshot]):
        if not self._listing or self._listing.id != listing.id:
            self._enrichment = None
            self._enrichment_searching = False
        self._listing = listing
        self._snapshots = snapshots
        self._update_display()

    def _update_display(self):
        listing = self._listing
        if not listing:
            return

        snap = self._snapshots[-1] if self._snapshots else None

        # Title
        addr = listing.address or "Unknown Address"
        if listing.unit:
            addr = f"Unit {listing.unit} — {addr}"
        self.title_label.setText(addr)

        # Subtitle
        parts = []
        if listing.building_name:
            parts.append(listing.building_name)
        if listing.city:
            parts.append(listing.city)
        if listing.neighbourhood:
            parts.append(listing.neighbourhood)
        if listing.mls_number:
            parts.append(f"MLS# {listing.mls_number}")
        self.subtitle_label.setText("  ·  ".join(parts) if parts else "")

        # Status
        if snap:
            status_text = snap.status.value if isinstance(snap.status, Status) else str(snap.status)
            colors = {
                "FOR_SALE": "#66bb6a", "SOLD": "#ef5350", "SOLD_CONDITIONAL": "#ffa726",
                "TERMINATED": "#ef5350", "EXPIRED": "#888", "DELISTED": "#888",
                "BLOCKED": "#ffa726", "UNKNOWN": "#666",
            }
            c = colors.get(status_text, "#666")
            self.status_label.setText(
                f'<span style="color:{c}; font-weight:bold; font-size:13px;">'
                f'● {status_text.replace("_", " ")}</span>'
            )
        else:
            self.status_label.setText('<span style="color:#666;">No data yet</span>')

        # Price hero
        if snap and snap.list_price:
            self.price_hero.setText(f"${snap.list_price:,.0f}")
            self.price_hero.setVisible(True)
        else:
            self.price_hero.setVisible(False)

        # Clear old cards
        while self.cards_layout.count():
            item = self.cards_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        self.commute_label.setVisible(False)
        if self._commute_tab is not None:
            self._commute_tab.deleteLater()
            self._commute_tab = None

        if not snap:
            self.qualify_label.setVisible(False)
            self.stats_label.setVisible(False)
            self.type_label.setVisible(False)
            self.pos_label.setVisible(False)
            self.broker_label.setVisible(False)
            self.tabs.setVisible(False)
            hdr_color = _header_bg_for_ptype(None, listing.url)
            self._header_container.setStyleSheet(
                f"QFrame {{ background: {hdr_color}; border-radius: 8px; padding: 0; margin: 0 0 6px 0; }}"
            )
            self.notes_edit.setPlainText(listing.user_notes or "")
            return

        cfg = self._finance_cfg
        fee_includes = snap.fee_includes or ""

        # Qualification badge + deal-breaker check
        breakers = check_deal_breakers(snap, self._profile) if self._profile else []
        if snap.list_price:
            q = qualifies(
                snap.list_price, snap.maintenance_fee or 0,
                snap.taxes_annual or 0, fee_includes, cfg
            )
            if breakers:
                self.qualify_label.setText(f"DEAL BREAKER: {breakers[0]}")
                self.qualify_label.setStyleSheet(QUALIFY_NO)
            elif q:
                self.qualify_label.setText("QUALIFIES")
                self.qualify_label.setStyleSheet(QUALIFY_YES)
            else:
                self.qualify_label.setText("DOES NOT QUALIFY")
                self.qualify_label.setStyleSheet(QUALIFY_NO)
            self.qualify_label.setVisible(True)
        else:
            if breakers:
                self.qualify_label.setText(f"DEAL BREAKER: {breakers[0]}")
                self.qualify_label.setStyleSheet(QUALIFY_NO)
                self.qualify_label.setVisible(True)
            else:
                self.qualify_label.setVisible(False)

        # Stats row
        ppsf = fpsf = None
        if snap.list_price:
            ppsf = price_per_sqft(snap.list_price, snap.sqft_min, snap.sqft_max)
            fpsf = fee_per_sqft(snap.maintenance_fee or 0, snap.sqft_min, snap.sqft_max)

        stats_parts = []
        if snap.beds:
            stats_parts.append(f"{snap.beds} Bed{'s' if snap.beds > 1 else ''}")
        if snap.baths:
            stats_parts.append(f"{snap.baths} Bath{'s' if snap.baths > 1 else ''}")
        sqft_str = self._format_sqft(snap.sqft_min, snap.sqft_max)
        if sqft_str != "—":
            stats_parts.append(f"{sqft_str} sqft")
        if snap.parking:
            stats_parts.append(self._clean_field(snap.parking))
        if stats_parts:
            self.stats_label.setText("  ·  ".join(stats_parts))
            self.stats_label.setVisible(True)
        else:
            self.stats_label.setVisible(False)

        if snap.property_type:
            self.type_label.setText(snap.property_type)
            self.type_label.setVisible(True)
        else:
            self.type_label.setVisible(False)

        if snap.power_of_sale:
            self.pos_label.setText("POWER OF SALE")
            self.pos_label.setVisible(True)
        else:
            self.pos_label.setVisible(False)

        if snap.broker:
            self.broker_label.setText(f"Listed by {self._clean_field(snap.broker)}")
            self.broker_label.setVisible(True)
        else:
            self.broker_label.setVisible(False)

        # Update header color based on property type
        hdr_color = _header_bg_for_ptype(
            snap.property_type, listing.url, snap.maintenance_fee
        )
        self._header_container.setStyleSheet(
            f"QFrame {{ background: {hdr_color}; border-radius: 8px; padding: 0; margin: 0 0 6px 0; }}"
        )

        # Build tabs
        self.tabs.clear()
        self.tabs.setVisible(True)

        # Tab 1: Highlights
        hl_tab = self._build_highlights_tab(snap)
        self.tabs.addTab(hl_tab, "Highlights")

        # Tab 2: Utilities & Inclusions
        util_tab = self._build_utilities_tab(snap, fee_includes)
        self.tabs.addTab(util_tab, "Utilities && Inclusions")

        # Tab 3: Budget
        budget_tab = self._build_budget_tab(snap, fee_includes)
        self.tabs.addTab(budget_tab, "Budget")

        # Tab 4: Value Score
        if snap.list_price:
            value_tab = self._build_value_tab(snap, fee_includes)
            self.tabs.addTab(value_tab, "Value")

        # Offer tab (combined offer + financials)
        if snap.list_price:
            offer_tab = self._build_offer_tab(snap, listing.id, fee_includes, listing.city or "")
            self.tabs.addTab(offer_tab, "Offer")

        if self._profile:
            self._commute_tab = CommuteTab(listing, self._profile)
            self._commute_tab.summary_ready.connect(self._show_commute_summary)
            self.tabs.addTab(self._commute_tab, "Commute")

        # Non-tab cards
        if snap.parse_errors:
            err_card = self._make_card("Parse Warnings")
            err_label = QLabel(snap.parse_errors)
            err_label.setWordWrap(True)
            err_label.setStyleSheet("color: #ef5350; font-size: 12px;")
            err_card.layout().addWidget(err_label)
            self.cards_layout.addWidget(err_card)

        if self._profile:
            warnings = self._check_timeline_warnings(snap)
            if warnings:
                tl_card = self._make_card("Timeline")
                for w in warnings:
                    wl = QLabel(w["text"])
                    wl.setWordWrap(True)
                    color = "#ffa726" if w["level"] == "warn" else "#ef5350"
                    wl.setStyleSheet(f"color: {color}; font-size: 12px; padding: 2px 0;")
                    tl_card.layout().addWidget(wl)
                self.cards_layout.addWidget(tl_card)

        if self._cross_ref is not None and listing:
            self._build_crossref_card(listing, self._cross_ref)
        if (self._enrichment or self._enrichment_searching) and listing:
            self._build_enrichment_card(listing)
        if listing.id in self._area_news or listing.id in self._area_news_searching:
            self._build_area_news_card(listing)

        self.notes_edit.setPlainText(listing.user_notes or "")
        self._update_chart()

    # --- Tab builders ---

    def _build_highlights_tab(self, snap: Snapshot) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(4, 4, 4, 4)

        grid = QGridLayout()
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        grid.setHorizontalSpacing(24)
        grid.setVerticalSpacing(6)

        items = [
            ("Balcony", self._clean_field(snap.outdoor_space) if snap.outdoor_space else "—"),
            ("Exposure", self._sanitize_exposure(snap.exposure)),
            ("Locker", self._clean_field(snap.locker) if snap.locker else "—"),
            ("Rent Controlled", snap.rent_controlled or "—"),
            ("Possession Date", snap.possession_date or "—"),
            ("Days on Market", f"{snap.days_on_market} days" if snap.days_on_market else "—"),
            ("Year Built", str(snap.year_built) if snap.year_built else "—"),
            ("Building Age", f"{date.today().year - snap.year_built} years" if snap.year_built else "—"),
        ]

        for i, (label, value) in enumerate(items):
            col = (i % 2) * 2
            r = i // 2
            grid.addWidget(self._hl_key(label), r, col)
            grid.addWidget(self._hl_val(value), r, col + 1)

        layout.addLayout(grid)
        layout.addStretch()
        return w

    def _build_utilities_tab(self, snap: Snapshot, fee_includes: str) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(4, 4, 4, 4)

        grid = QGridLayout()
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        grid.setHorizontalSpacing(24)
        grid.setVerticalSpacing(6)

        includes_lower = fee_includes.lower() if fee_includes else ""

        items = [
            ("Hydro", "Included" if "hydro" in includes_lower else "Not Included"),
            ("Heat", "Included" if "heat" in includes_lower else "Not Included"),
            ("Water", "Included" if "water" in includes_lower else "Not Included"),
            ("Air Conditioning", "Included" if "air" in includes_lower else "Not Included"),
            ("Parking", self._clean_field(snap.parking) if snap.parking else "Not Included"),
            ("Building Insurance", "Included" if "insurance" in includes_lower else "Not Included"),
            ("Ensuite Laundry", snap.ensuite_laundry or "—"),
        ]

        for i, (label, value) in enumerate(items):
            col = (i % 2) * 2
            r = i // 2
            grid.addWidget(self._hl_key(label), r, col)
            v = self._hl_val(value)
            if value == "Included":
                v.setStyleSheet("color: #66bb6a; font-size: 13px; font-weight: bold; padding: 2px 0;")
            elif value == "Not Included":
                v.setStyleSheet("color: #ef5350; font-size: 13px; font-weight: bold; padding: 2px 0;")
            grid.addWidget(v, r, col + 1)

        layout.addLayout(grid)

        if fee_includes:
            sep = QFrame()
            sep.setFrameShape(QFrame.Shape.HLine)
            sep.setStyleSheet("color: #333; margin: 8px 0;")
            layout.addWidget(sep)
            inc_lbl = QLabel(f"Fee includes: {self._clean_field(fee_includes)}")
            inc_lbl.setStyleSheet("color: #888; font-size: 11px;")
            inc_lbl.setWordWrap(True)
            layout.addWidget(inc_lbl)

        layout.addStretch()
        return w

    def _build_budget_tab(self, snap: Snapshot, fee_includes: str) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(4, 4, 4, 4)

        grid = QGridLayout()
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        grid.setHorizontalSpacing(24)
        grid.setVerticalSpacing(6)

        ppsf = price_per_sqft(snap.list_price, snap.sqft_min, snap.sqft_max) if snap.list_price else None

        items = [
            ("Maintenance Fees", f"${snap.maintenance_fee:,.0f}/mo" if snap.maintenance_fee else "—"),
            ("Taxes", f"${snap.taxes_annual:,.0f}/yr" if snap.taxes_annual else "—"),
            ("Cost Per Sqft", f"${ppsf:,.0f}" if ppsf else "—"),
            ("Fee Per Sqft", f"${fee_per_sqft(snap.maintenance_fee or 0, snap.sqft_min, snap.sqft_max):,.2f}" if snap.maintenance_fee else "—"),
        ]

        for i, (label, value) in enumerate(items):
            col = (i % 2) * 2
            r = i // 2
            grid.addWidget(self._hl_key(label), r, col)
            grid.addWidget(self._hl_val(value), r, col + 1)

        layout.addLayout(grid)

        # Monthly budget analysis
        if self._profile and self._profile.monthly_allin_target > 0 and snap.list_price:
            budget_target = self._profile.monthly_allin_target
            cfg = self._finance_cfg
            mcc = monthly_carrying_cost(
                snap.list_price, snap.maintenance_fee or 0,
                snap.taxes_annual or 0, fee_includes, cfg
            )

            sep = QFrame()
            sep.setFrameShape(QFrame.Shape.HLine)
            sep.setStyleSheet("color: #333; margin: 8px 0;")
            layout.addWidget(sep)

            budget_hdr = QLabel(f"Monthly Budget Target: ${budget_target:,.0f}/mo")
            budget_hdr.setStyleSheet(
                "color: #81d4fa; font-size: 13px; font-weight: bold; margin-bottom: 4px;"
            )
            layout.addWidget(budget_hdr)

            budget_grid = QGridLayout()
            budget_grid.setColumnStretch(1, 1)
            budget_grid.setHorizontalSpacing(12)
            budget_grid.setVerticalSpacing(6)
            brow = 0

            if mcc is not None:
                diff = mcc - budget_target
                if diff <= 0:
                    status_txt = f"${abs(diff):,.0f}/mo under budget"
                    status_color = "#66bb6a"
                else:
                    status_txt = f"${diff:,.0f}/mo over budget"
                    status_color = "#ef5350"
                budget_grid.addWidget(self._hl_key("Current vs Budget"), brow, 0)
                sv = self._hl_val(status_txt)
                sv.setStyleSheet(f"color: {status_color}; font-size: 14px; font-weight: bold;")
                budget_grid.addWidget(sv, brow, 1)
                brow += 1

            if mcc is not None and mcc > budget_target:
                dp_needed = down_payment_for_budget(
                    snap.list_price, snap.maintenance_fee or 0,
                    snap.taxes_annual or 0, fee_includes,
                    budget_target, cfg,
                )
                if dp_needed is not None:
                    budget_grid.addWidget(self._hl_key("Down Payment Needed"), brow, 0)
                    dp_lbl = self._hl_val(
                        f"${dp_needed:,.0f} ({dp_needed/snap.list_price*100:.0f}%)"
                    )
                    dp_extra = dp_needed - cfg.down_payment_available
                    if dp_extra > 0:
                        dp_lbl.setStyleSheet("color: #ef5350; font-size: 14px; font-weight: bold;")
                    else:
                        dp_lbl.setStyleSheet("color: #66bb6a; font-size: 14px; font-weight: bold;")
                    budget_grid.addWidget(dp_lbl, brow, 1)
                    brow += 1

                price_tgt = price_target_for_budget(
                    snap.maintenance_fee or 0, snap.taxes_annual or 0,
                    fee_includes, budget_target, cfg,
                )
                if price_tgt > 0:
                    budget_grid.addWidget(self._hl_key("Price Target"), brow, 0)
                    reduction = snap.list_price - price_tgt
                    pt_lbl = self._hl_val(f"${price_tgt:,.0f} (-${reduction:,.0f})")
                    pt_lbl.setStyleSheet("color: #ffa726; font-size: 14px; font-weight: bold;")
                    budget_grid.addWidget(pt_lbl, brow, 1)

            layout.addLayout(budget_grid)

        layout.addStretch()
        return w

    def _build_value_tab(self, snap: Snapshot, fee_includes: str) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(4, 4, 4, 4)

        cfg = self._finance_cfg
        vm = compute_value_metrics(
            snap.list_price, snap.maintenance_fee, snap.taxes_annual,
            fee_includes, snap.sqft_min, snap.sqft_max, snap.beds, cfg,
        )

        # Composite score header
        score_color = "#66bb6a" if (vm.score or 0) >= 60 else (
            "#ffa726" if (vm.score or 0) >= 40 else "#ef5350"
        )
        score_label = QLabel(f"Value Score: {vm.score:.0f}" if vm.score is not None else "Value Score: —")
        score_label.setStyleSheet(
            f"color: {score_color}; font-size: 22px; font-weight: bold; margin-bottom: 8px;"
        )
        score_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(score_label)

        # Per-sqft metrics grid
        grid = QGridLayout()
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        grid.setHorizontalSpacing(24)
        grid.setVerticalSpacing(6)

        metrics = [
            ("Price / sqft", f"${vm.price_psf:,.0f}" if vm.price_psf else "—"),
            ("Fee / sqft", f"${vm.fee_psf:.2f}" if vm.fee_psf else "—"),
            ("Tax / sqft", f"${vm.tax_psf:.2f}" if vm.tax_psf else "—"),
            ("Amenity Cost / sqft", f"${vm.amenity_cost:.2f}" if vm.amenity_cost else "—"),
        ]
        for i, (label, value) in enumerate(metrics):
            col = (i % 2) * 2
            r = i // 2
            grid.addWidget(self._hl_key(label), r, col)
            grid.addWidget(self._hl_val(value), r, col + 1)

        layout.addLayout(grid)

        # Score breakdown
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color: #333; margin: 8px 0;")
        layout.addWidget(sep)

        breakdown_hdr = QLabel("Score Breakdown")
        breakdown_hdr.setStyleSheet(
            "color: #81d4fa; font-size: 13px; font-weight: bold; margin-bottom: 4px;"
        )
        layout.addWidget(breakdown_hdr)

        bars_layout = QVBoxLayout()
        bars_layout.setSpacing(4)

        components = [
            ("Price/sqft", vm.price_psf_score, 0.25),
            ("Monthly Cost", vm.monthly_score, 0.20),
            ("Fee/sqft", vm.fee_psf_score, 0.15),
            ("Tax/sqft", vm.tax_psf_score, 0.10),
            ("Amenity Cost", vm.amenity_score, 0.10),
            ("Size", vm.size_score, 0.10),
            ("Bedrooms", vm.bedroom_score, 0.10),
        ]

        for name, sub_score, weight in components:
            row = QHBoxLayout()
            row.setSpacing(8)
            lbl = QLabel(f"{name} ({weight*100:.0f}%)")
            lbl.setFixedWidth(150)
            lbl.setStyleSheet("color: #aaa; font-size: 12px;")
            row.addWidget(lbl)

            bar_bg = QFrame()
            bar_bg.setFixedHeight(14)
            bar_bg.setStyleSheet("background: #1a1a2e; border-radius: 3px;")
            bar_inner = QFrame(bar_bg)
            pct = max(0, min(100, sub_score))
            bar_color = "#66bb6a" if pct >= 60 else ("#ffa726" if pct >= 40 else "#ef5350")
            bar_inner.setStyleSheet(
                f"background: {bar_color}; border-radius: 3px;"
            )
            bar_inner.setFixedHeight(14)
            bar_inner.setFixedWidth(max(1, int(pct * 1.8)))
            row.addWidget(bar_bg, 1)

            score_val = QLabel(f"{sub_score:.0f}")
            score_val.setFixedWidth(30)
            score_val.setAlignment(Qt.AlignmentFlag.AlignRight)
            score_val.setStyleSheet(f"color: {bar_color}; font-size: 12px; font-weight: bold;")
            row.addWidget(score_val)

            bars_layout.addLayout(row)

        layout.addLayout(bars_layout)
        layout.addStretch()
        return w

    def _build_offer_tab(self, snap: Snapshot, listing_id: int, fee_includes: str, city: str = "") -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(4, 4, 4, 4)

        cfg = self._finance_cfg
        dom = snap.days_on_market
        price = snap.list_price
        sqft = snap.sqft_max or snap.sqft_min or 0

        peer_stats = listing_db.get_peer_stats(
            listing_id, city=city, beds=snap.beds or 0,
            list_price=price, sqft=sqft,
        )

        # DOM-based bracket
        if dom is not None and dom > 0:
            if dom <= 7:
                pct_range = (97, 99)
                advice = "Just listed -- seller expects full price. Offer close to asking."
            elif dom <= 21:
                pct_range = (95, 98)
                advice = "Fresh listing. Minor room to negotiate."
            elif dom <= 45:
                pct_range = (92, 96)
                advice = "Sitting a while. Moderate negotiating room."
            elif dom <= 90:
                pct_range = (88, 94)
                advice = "Stale listing. Seller likely motivated."
            elif dom <= 180:
                pct_range = (82, 90)
                advice = "Very stale. Strong negotiating position."
            else:
                pct_range = (75, 85)
                advice = "Extremely stale. Aggressive lowball may work."
        else:
            pct_range = (92, 98)
            advice = "Days on market unknown. Use peer data and your judgment."

        if peer_stats and peer_stats.get("discount_pct", 0) < -5:
            premium = abs(peer_stats["discount_pct"])
            adj = min(int(premium / 3), 5)
            pct_range = (max(70, pct_range[0] - adj), max(70, pct_range[1] - adj))

        # --- Market context ---
        if dom is not None and dom > 0:
            dom_lbl = QLabel(f"Days on Market: {dom}")
            dom_lbl.setStyleSheet("color: #81d4fa; font-size: 13px; font-weight: bold;")
            layout.addWidget(dom_lbl)

        if peer_stats:
            disc = peer_stats["discount_pct"]
            if disc > 0:
                peer_text = (f"Priced {disc:.1f}% below peers "
                             f"(${peer_stats['this_ppsf']:,.0f} vs ${peer_stats['avg_ppsf']:,.0f}/sqft, "
                             f"{peer_stats['peer_count']} similar in {peer_stats['city']})")
                peer_color = "#66bb6a"
            elif disc < -1:
                peer_text = (f"Priced {abs(disc):.1f}% above peers "
                             f"(${peer_stats['this_ppsf']:,.0f} vs ${peer_stats['avg_ppsf']:,.0f}/sqft, "
                             f"{peer_stats['peer_count']} similar in {peer_stats['city']})")
                peer_color = "#ef5350"
            else:
                peer_text = (f"Priced at market "
                             f"(${peer_stats['this_ppsf']:,.0f} vs ${peer_stats['avg_ppsf']:,.0f}/sqft, "
                             f"{peer_stats['peer_count']} similar in {peer_stats['city']})")
                peer_color = "#aaa"
            peer_lbl = QLabel(peer_text)
            peer_lbl.setStyleSheet(f"color: {peer_color}; font-size: 11px;")
            peer_lbl.setWordWrap(True)
            layout.addWidget(peer_lbl)

        advice_lbl = QLabel(advice)
        advice_lbl.setStyleSheet("color: #ffa726; font-size: 12px; margin: 4px 0 8px 0;")
        advice_lbl.setWordWrap(True)
        layout.addWidget(advice_lbl)

        # --- Offer price slider + editable percentage ---
        price_hdr = QLabel("Your Offer")
        price_hdr.setStyleSheet("color: #81d4fa; font-size: 12px; font-weight: bold;")
        layout.addWidget(price_hdr)

        price_lo = int(price * 0.70)
        price_hi = int(price * 1.05)
        default_pct = (pct_range[0] + pct_range[1]) // 2
        default_offer = int(price * default_pct / 100)

        offer_row = QHBoxLayout()
        self._offer_slider = QSlider(Qt.Orientation.Horizontal)
        self._offer_slider.setStyleSheet(SLIDER_STYLE)
        self._offer_slider.setRange(price_lo, price_hi)
        self._offer_slider.setValue(default_offer)
        self._offer_slider.setSingleStep(1000)
        self._offer_slider.setPageStep(5000)

        self._offer_display = QLabel(f"${default_offer:,}")
        self._offer_display.setStyleSheet("color: #fff; font-size: 14px; font-weight: bold; min-width: 100px;")
        self._offer_display.setAlignment(Qt.AlignmentFlag.AlignRight)

        self._offer_pct_spin = QSpinBox()
        self._offer_pct_spin.setRange(70, 105)
        self._offer_pct_spin.setValue(default_pct)
        self._offer_pct_spin.setSuffix("%")
        self._offer_pct_spin.setStyleSheet(
            "QSpinBox { background: #2a2a2a; color: #fff; border: 1px solid #555; "
            "border-radius: 3px; padding: 2px 4px; font-size: 13px; font-weight: bold; "
            "min-width: 60px; max-width: 70px; }"
        )
        self._offer_pct_spin.setAlignment(Qt.AlignmentFlag.AlignCenter)

        offer_row.addWidget(self._offer_slider, stretch=1)
        offer_row.addWidget(self._offer_display)
        offer_row.addWidget(self._offer_pct_spin)
        layout.addLayout(offer_row)

        # --- Suggested price (clickable) ---
        suggested_price = self._compute_suggested_offer(price, dom, peer_stats, snap)
        self._suggested_offer_price = suggested_price

        suggest_row = QHBoxLayout()
        range_lo_dollar = int(price * pct_range[0] / 100)
        range_hi_dollar = int(price * pct_range[1] / 100)
        range_lbl = QLabel(
            f"Range: ${range_lo_dollar:,} -- ${range_hi_dollar:,} "
            f"({pct_range[0]}% -- {pct_range[1]}%)"
        )
        range_lbl.setStyleSheet("color: #888; font-size: 11px;")
        suggest_row.addWidget(range_lbl)

        if suggested_price:
            suggest_pct = int(suggested_price / price * 100) if price > 0 else 0
            self._suggest_btn = QPushButton(f"Suggested: ${suggested_price:,} ({suggest_pct}%)")
            self._suggest_btn.setStyleSheet(
                "QPushButton { background: #2d4a1e; color: #a5d6a7; border: 1px solid #4caf50; "
                "border-radius: 4px; padding: 3px 10px; font-size: 11px; font-weight: bold; }"
                "QPushButton:hover { background: #3a6027; border-color: #66bb6a; }"
            )
            self._suggest_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self._suggest_btn.clicked.connect(self._apply_suggested_offer)
            suggest_row.addWidget(self._suggest_btn)
        suggest_row.addStretch()
        layout.addLayout(suggest_row)

        # --- Down payment slider with percentage spinner ---
        dp_hdr = QLabel("Down Payment")
        dp_hdr.setStyleSheet("color: #81d4fa; font-size: 12px; font-weight: bold; margin-top: 4px;")
        layout.addWidget(dp_hdr)

        min_dp = int(down_payment_required(default_offer))
        max_dp = int(min(cfg.down_payment_available, default_offer * 0.99))
        if max_dp < min_dp:
            max_dp = min_dp

        dp_row = QHBoxLayout()
        self._dp_slider = QSlider(Qt.Orientation.Horizontal)
        self._dp_slider.setStyleSheet(SLIDER_STYLE)
        self._dp_slider.setRange(min_dp, max_dp)
        self._dp_slider.setValue(min_dp)
        self._dp_slider.setSingleStep(1000)
        self._dp_slider.setPageStep(5000)

        self._dp_display = QLabel(f"${min_dp:,}")
        self._dp_display.setStyleSheet("color: #fff; font-size: 14px; font-weight: bold; min-width: 90px;")
        self._dp_display.setAlignment(Qt.AlignmentFlag.AlignRight)

        min_dp_pct = max(5, int(min_dp / default_offer * 100)) if default_offer > 0 else 5
        max_dp_pct = int(max_dp / default_offer * 100) if default_offer > 0 else 99
        self._dp_pct_spin = QSpinBox()
        self._dp_pct_spin.setRange(min_dp_pct, min(max_dp_pct, 99))
        self._dp_pct_spin.setValue(min_dp_pct)
        self._dp_pct_spin.setSuffix("%")
        self._dp_pct_spin.setStyleSheet(
            "QSpinBox { background: #2a2a2a; color: #fff; border: 1px solid #555; "
            "border-radius: 3px; padding: 2px 4px; font-size: 13px; font-weight: bold; "
            "min-width: 60px; max-width: 70px; }"
        )
        self._dp_pct_spin.setAlignment(Qt.AlignmentFlag.AlignCenter)

        dp_row.addWidget(self._dp_slider, stretch=1)
        dp_row.addWidget(self._dp_display)
        dp_row.addWidget(self._dp_pct_spin)
        layout.addLayout(dp_row)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color: #333; margin: 6px 0;")
        layout.addWidget(sep)

        # --- Results area ---
        self._offer_results = QWidget()
        self._offer_results_layout = QVBoxLayout(self._offer_results)
        self._offer_results_layout.setContentsMargins(0, 2, 0, 0)
        layout.addWidget(self._offer_results)

        self._offer_list_price = price
        self._offer_snap = snap
        self._offer_fee_includes = fee_includes

        self._offer_slider.valueChanged.connect(self._on_offer_slider_changed)
        self._offer_pct_spin.valueChanged.connect(self._on_offer_pct_changed)
        self._dp_slider.valueChanged.connect(self._on_dp_slider_changed)
        self._dp_pct_spin.valueChanged.connect(self._on_dp_pct_changed)
        self._recalc_offer()

        layout.addStretch()
        return w

    def _on_offer_slider_changed(self):
        if self._slider_updating:
            return
        self._slider_updating = True

        price = self._offer_list_price
        offer = self._offer_slider.value()
        pct = int(offer / price * 100) if price > 0 else 100
        self._offer_pct_spin.blockSignals(True)
        self._offer_pct_spin.setValue(pct)
        self._offer_pct_spin.blockSignals(False)

        self._sync_dp_range(offer)
        self._slider_updating = False
        self._recalc_offer()

    def _on_offer_pct_changed(self):
        if self._slider_updating:
            return
        self._slider_updating = True

        price = self._offer_list_price
        pct = self._offer_pct_spin.value()
        offer = int(price * pct / 100)
        self._offer_slider.blockSignals(True)
        self._offer_slider.setValue(offer)
        self._offer_slider.blockSignals(False)

        self._sync_dp_range(offer)
        self._slider_updating = False
        self._recalc_offer()

    def _on_dp_slider_changed(self):
        if self._slider_updating:
            return
        self._slider_updating = True

        offer = self._offer_slider.value()
        dp = self._dp_slider.value()
        pct = int(dp / offer * 100) if offer > 0 else 0
        self._dp_display.setText(f"${dp:,}")
        self._dp_pct_spin.blockSignals(True)
        self._dp_pct_spin.setValue(pct)
        self._dp_pct_spin.blockSignals(False)

        self._slider_updating = False
        self._recalc_offer()

    def _on_dp_pct_changed(self):
        if self._slider_updating:
            return
        self._slider_updating = True

        offer = self._offer_slider.value()
        pct = self._dp_pct_spin.value()
        dp = int(offer * pct / 100)
        min_dp = int(down_payment_required(offer))
        if dp < min_dp:
            dp = min_dp
        self._dp_slider.blockSignals(True)
        self._dp_slider.setValue(dp)
        self._dp_slider.blockSignals(False)
        self._dp_display.setText(f"${dp:,}")

        self._slider_updating = False
        self._recalc_offer()

    def _sync_dp_range(self, offer: int):
        cfg = self._finance_cfg
        min_dp = int(down_payment_required(offer))
        max_dp = int(min(cfg.down_payment_available, offer * 0.99))
        if max_dp < min_dp:
            max_dp = min_dp
        dp = self._dp_slider.value()
        self._dp_slider.blockSignals(True)
        self._dp_slider.setRange(min_dp, max_dp)
        if dp < min_dp:
            self._dp_slider.setValue(min_dp)
            dp = min_dp
        self._dp_slider.blockSignals(False)

        min_pct = max(5, int(min_dp / offer * 100)) if offer > 0 else 5
        max_pct = int(max_dp / offer * 100) if offer > 0 else 99
        cur_pct = int(dp / offer * 100) if offer > 0 else min_pct
        self._dp_pct_spin.blockSignals(True)
        self._dp_pct_spin.setRange(min_pct, min(max_pct, 99))
        self._dp_pct_spin.setValue(cur_pct)
        self._dp_pct_spin.blockSignals(False)

        self._dp_display.setText(f"${dp:,}")

    def _compute_suggested_offer(
        self, price: float, dom: int | None, peer_stats: dict, snap: Snapshot
    ) -> int | None:
        """Compute a suggested offer price from peers, DOM, and amenities."""
        if not price or price <= 0:
            return None

        base_pct = 95.0

        # DOM adjustment: longer on market → lower offer
        if dom is not None and dom > 0:
            if dom <= 7:
                base_pct = 98.0
            elif dom <= 21:
                base_pct = 96.5
            elif dom <= 45:
                base_pct = 94.0
            elif dom <= 90:
                base_pct = 91.0
            elif dom <= 180:
                base_pct = 86.0
            else:
                base_pct = 80.0

        # Peer price-per-sqft adjustment
        if peer_stats and peer_stats.get("avg_ppsf"):
            disc = peer_stats.get("discount_pct", 0)
            if disc < -3:
                # Priced above peers — reduce further
                base_pct -= min(abs(disc) / 2, 5)
            elif disc > 5:
                # Priced below peers — not much room to negotiate
                base_pct += min(disc / 4, 3)

        # Amenity penalties: missing desirable features
        penalties = 0
        if snap.parking and snap.parking.lower() in ("none", "no", "0", "n/a"):
            penalties += 1.5
        if snap.locker and snap.locker.lower() in ("none", "no", "n/a"):
            penalties += 0.5
        if snap.balcony and snap.balcony.lower() in ("none", "no", "n/a"):
            penalties += 1.0
        base_pct -= penalties

        base_pct = max(70.0, min(base_pct, 100.0))
        suggested = int(price * base_pct / 100)
        # Round to nearest $1000
        suggested = round(suggested / 1000) * 1000
        return suggested

    def _apply_suggested_offer(self):
        """Set the offer slider to the computed suggested price."""
        if not hasattr(self, '_suggested_offer_price') or not self._suggested_offer_price:
            return
        price = self._offer_list_price
        suggested = self._suggested_offer_price
        pct = int(suggested / price * 100) if price > 0 else 95

        self._slider_updating = True
        self._offer_slider.blockSignals(True)
        self._offer_pct_spin.blockSignals(True)
        self._offer_slider.setValue(suggested)
        self._offer_pct_spin.setValue(pct)
        self._offer_slider.blockSignals(False)
        self._offer_pct_spin.blockSignals(False)
        self._sync_dp_range(suggested)
        self._slider_updating = False
        self._recalc_offer()

    def _recalc_offer(self):
        offer = self._offer_slider.value()
        price = self._offer_list_price
        snap = self._offer_snap
        fee_includes = self._offer_fee_includes
        cfg = self._finance_cfg

        pct = offer / price * 100 if price > 0 else 0
        self._offer_display.setText(f"${offer:,}")

        savings = int(price - offer)
        dp_amount = self._dp_slider.value()

        while self._offer_results_layout.count():
            item = self._offer_results_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            elif item.layout():
                self._clear_layout(item.layout())

        # --- Offer summary ---
        grid = QGridLayout()
        grid.setColumnStretch(1, 1)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(4)

        val_style = "color: #e0e0e0; font-size: 13px; font-weight: bold;"
        total_style = "color: #81d4fa; font-size: 14px; font-weight: bold;"

        row_idx = 0
        grid.addWidget(self._hl_key("List Price"), row_idx, 0)
        grid.addWidget(self._hl_val(f"${price:,.0f}"), row_idx, 1)

        row_idx += 1
        grid.addWidget(self._hl_key("Your Offer"), row_idx, 0)
        offer_lbl = self._hl_val(f"${offer:,.0f}")
        offer_lbl.setStyleSheet("color: #81d4fa; font-size: 15px; font-weight: bold;")
        grid.addWidget(offer_lbl, row_idx, 1)

        row_idx += 1
        grid.addWidget(self._hl_key("Savings"), row_idx, 0)
        sav_lbl = self._hl_val(f"${savings:,.0f}")
        if savings > 0:
            sav_lbl.setStyleSheet("color: #66bb6a; font-size: 13px; font-weight: bold;")
        elif savings < 0:
            sav_lbl.setStyleSheet("color: #ef5350; font-size: 13px; font-weight: bold;")
        grid.addWidget(sav_lbl, row_idx, 1)

        self._offer_results_layout.addLayout(grid)

        # --- Mortgage breakdown ---
        scenario = _calc_dp_scenario(
            offer, dp_amount,
            snap.maintenance_fee or 0, snap.taxes_annual or 0,
            fee_includes, cfg,
        )

        rate_lbl = QLabel(
            f"Rate: {cfg.contract_rate*100:.2f}%  ·  "
            f"Amortization: {cfg.amortization_years}yr"
        )
        rate_lbl.setStyleSheet("color: #aaa; font-size: 11px; padding: 4px 0 2px 0;")
        self._offer_results_layout.addWidget(rate_lbl)

        fin_grid = QGridLayout()
        fin_grid.setHorizontalSpacing(12)
        fin_grid.setVerticalSpacing(3)
        fin_grid.setColumnStretch(0, 1)
        fin_grid.setColumnStretch(1, 0)

        fin_rows = [
            ("Mortgage", f"${scenario['total_mortgage']:,.0f}"),
        ]
        if scenario["cmhc_insurance"] > 0:
            fin_rows.append(("CMHC Insurance", f"${scenario['cmhc_insurance']:,.0f}"))
        fin_rows += [
            ("Monthly P+I", f"${scenario['monthly_pi']:,.0f}"),
            ("Monthly Total", f"${scenario['monthly_total']:,.0f}"),
        ]

        for i, (label, value) in enumerate(fin_rows):
            fin_grid.addWidget(self._hl_key(label), i, 0)
            vl = QLabel(value)
            vl.setAlignment(Qt.AlignmentFlag.AlignRight)
            vl.setStyleSheet(total_style if label == "Monthly Total" else val_style)
            fin_grid.addWidget(vl, i, 1)

        self._offer_results_layout.addLayout(fin_grid)

        # --- Extra items ---
        extras_grid = QGridLayout()
        extras_grid.setColumnStretch(1, 1)
        extras_grid.setColumnStretch(3, 1)
        extras_grid.setHorizontalSpacing(20)
        extras_grid.setVerticalSpacing(4)

        max_p = max_qualifying_price(
            snap.maintenance_fee or 0, snap.taxes_annual or 0,
            fee_includes, cfg
        )
        cash = cash_left_after_close(offer, cfg)
        ltt = ontario_land_transfer_tax(offer)

        extra_items = [
            ("Max Qualifying", f"${max_p:,.0f}"),
            ("Cash After Close", f"${cash:,.0f}"),
            ("Land Transfer Tax", f"${ltt:,.0f}"),
            ("Total Interest", f"${scenario['total_interest']:,.0f}"),
        ]
        if cfg.first_time_buyer:
            rebate = first_time_buyer_rebate(ltt)
            extra_items.append(("FTB Rebate", f"-${rebate:,.0f}"))

        for i, (label, value) in enumerate(extra_items):
            col = (i % 2) * 2
            r = i // 2
            extras_grid.addWidget(self._hl_key(label), r, col)
            vl = self._hl_val(value)
            if label == "Cash After Close" and cash < 0:
                vl.setStyleSheet("color: #ef5350; font-size: 14px; font-weight: bold;")
            extras_grid.addWidget(vl, r, col + 1)

        self._offer_results_layout.addLayout(extras_grid)

    # --- Helpers ---

    def _clear_layout(self, layout):
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            elif item.layout():
                self._clear_layout(item.layout())

    def _check_timeline_warnings(self, snap: Optional[Snapshot]) -> list[dict]:
        warnings = []
        today = date.today()
        p = self._profile

        if p.close_by_date:
            try:
                close_dt = date.fromisoformat(p.close_by_date)
                days_left = (close_dt - today).days
                if days_left < 0:
                    warnings.append({"level": "error", "text": f"Close-by date has passed ({p.close_by_date})"})
                elif days_left <= 30:
                    warnings.append({"level": "error", "text": f"Only {days_left} days until close-by deadline ({close_dt.strftime('%b %d, %Y')})"})
                elif days_left <= 90:
                    warnings.append({"level": "warn", "text": f"{days_left} days until close-by deadline ({close_dt.strftime('%b %d, %Y')})"})
            except ValueError:
                pass

        if p.earliest_offer_date:
            try:
                earliest = date.fromisoformat(p.earliest_offer_date)
                if today < earliest:
                    days_until = (earliest - today).days
                    warnings.append({"level": "warn", "text": f"Cannot offer yet — earliest offer date is {earliest.strftime('%b %d, %Y')} ({days_until} days)"})
            except ValueError:
                pass

        if p.latest_offer_date:
            try:
                latest = date.fromisoformat(p.latest_offer_date)
                days_left = (latest - today).days
                if days_left < 0:
                    warnings.append({"level": "error", "text": f"Latest offer date has passed ({latest.strftime('%b %d, %Y')})"})
                elif days_left <= 14:
                    warnings.append({"level": "warn", "text": f"Only {days_left} days until latest offer deadline ({latest.strftime('%b %d, %Y')})"})
            except ValueError:
                pass

        return warnings

    def _make_card(self, title: str) -> QGroupBox:
        card = QGroupBox(title)
        card.setStyleSheet(CARD_STYLE)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(8, 4, 8, 8)
        return card

    def _key_label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(KEY_LABEL)
        return lbl

    def _val_label(self, text: str) -> QLabel:
        lbl = QLabel(self._clean_field(text))
        lbl.setStyleSheet(VAL_LABEL)
        lbl.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        return lbl

    @staticmethod
    def _sanitize_exposure(raw: Optional[str]) -> str:
        if not raw or raw == "—":
            return "—"
        import re
        cleaned = re.sub(r'(?:normal|flex-direction|style|display|none|inherit|column|row)[;:\s]*', '', raw, flags=re.IGNORECASE)
        cleaned = re.sub(r'[{};]', '', cleaned).strip()
        if not cleaned:
            return "—"
        valid = re.findall(r'\b(North|South|East|West|N|S|E|W|NE|NW|SE|SW)\b', cleaned, re.IGNORECASE)
        if valid:
            return ", ".join(v.title() for v in valid)
        return cleaned[:30] if len(cleaned) <= 30 else cleaned[:30]

    def _hl_key(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet("color: #999; font-size: 12px; padding: 2px 0;")
        return lbl

    def _hl_val(self, text: str) -> QLabel:
        lbl = QLabel(self._clean_field(text))
        lbl.setStyleSheet("color: #fff; font-size: 14px; font-weight: bold; padding: 2px 0;")
        lbl.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        return lbl

    @staticmethod
    def _clean_field(raw: str) -> str:
        """Strip JSON/structured junk from scraped fields into human-readable text."""
        import json
        import re
        if not raw or raw == "—":
            return raw

        unescaped = raw.replace('\\"', '"').replace("\\'", "'")

        for attempt in (raw, unescaped, f"[{unescaped}]"):
            try:
                data = json.loads(attempt)
                names = []
                items = data if isinstance(data, list) else [data]
                for item in items:
                    if isinstance(item, dict):
                        n = item.get("name", "")
                        if n and isinstance(n, str):
                            n = re.sub(r'^Maintenance fee includes\s+', '', n, flags=re.IGNORECASE)
                            names.append(n)
                    elif isinstance(item, str):
                        names.append(item)
                if names:
                    return ", ".join(names)
            except (json.JSONDecodeError, TypeError):
                continue

        names = re.findall(r'"name"\s*:\s*"([^"]+)"', unescaped)
        if names:
            cleaned = []
            for n in names:
                n = re.sub(r'^Maintenance fee includes\s+', '', n, flags=re.IGNORECASE)
                cleaned.append(n)
            leading = re.match(r'^([A-Za-z][A-Za-z /&]+?)(?=[",{}\[\]\\])', unescaped)
            if leading:
                cleaned.insert(0, leading.group(1).strip())
            return ", ".join(cleaned)

        spaces_match = re.search(r'[Ss]paces["\s:]+(\d+)', unescaped)
        if spaces_match:
            n = spaces_match.group(1)
            kind_match = re.search(r'(?:UNDERGROUND|SURFACE|INDOOR|OUTDOOR|GARAGE)', unescaped, re.IGNORECASE)
            kind = kind_match.group(0).title() if kind_match else ""
            return f"{n} {kind}".strip() if kind else f"{n} space{'s' if int(n) != 1 else ''}"

        enums = re.findall(r'\b([A-Z]+(?:_[A-Z]+)+)\b', unescaped)
        if enums:
            readable = [e.replace('_', ' ').title() for e in enums]
            return ", ".join(readable)

        if re.match(r'^[\s",{}\[\]]+', unescaped) and re.search(r'"(?:url|logo|@type)"', unescaped):
            return "—"

        if any(c in raw for c in ('{', '":', '\\', '@type')):
            clean = re.sub(r'[{}\[\]"\\]', ' ', unescaped)
            clean = re.sub(r'@type\s*:\s*\S+', '', clean)
            clean = re.sub(r'https?://\S+', '', clean)
            clean = re.sub(r'\b(value|name|type|propertyListingState|soldDate|possession|url|logo)\s*:', '', clean)
            clean = re.sub(r'\b(true|false|null)\b', '', clean)
            clean = re.sub(r'[,;]+', ', ', clean)
            clean = re.sub(r'\s+', ' ', clean).strip(' ,')
            if clean and len(clean) > 2:
                return clean
            return "—"

        return raw

    def _build_crossref_card(self, listing: Listing, xref: Optional[CrossRefResult]):
        other = [l for l in xref.listings if l.id != listing.id] if xref else []
        if not other:
            return

        card = self._make_card(f"Cross-Check ({len(other) + 1} sources)")

        match_label = QLabel(f"Matched by: {xref.match_reason}")
        match_label.setStyleSheet("color: #81d4fa; font-size: 12px; margin-bottom: 4px;")
        card.layout().addWidget(match_label)

        for ol in other:
            ol_snap = xref.snapshots.get(ol.id)
            price_str = f"${ol_snap.list_price:,.0f}" if ol_snap and ol_snap.list_price else "no price"
            source_label = QLabel(f"  {ol.site}: {ol.address or ol.url[:40]} — {price_str}")
            source_label.setStyleSheet("color: #aaa; font-size: 11px;")
            source_label.setWordWrap(True)
            card.layout().addWidget(source_label)

        if xref.comparisons:
            sep = QFrame()
            sep.setFrameShape(QFrame.Shape.HLine)
            sep.setStyleSheet("color: #333; margin: 6px 0;")
            card.layout().addWidget(sep)

            header_label = QLabel("Field Verification:")
            header_label.setStyleSheet("color: #888; font-size: 11px; font-weight: bold; margin-top: 4px;")
            card.layout().addWidget(header_label)

            grid = QGridLayout()
            grid.setColumnStretch(1, 1)

            agreed_count = sum(1 for c in xref.comparisons if c.agreed)
            total = len(xref.comparisons)

            row = 0
            for comp in xref.comparisons:
                if comp.agreed:
                    icon = "OK"
                    icon_style = "color: #66bb6a; font-size: 12px; font-weight: bold;"
                else:
                    icon = "!!"
                    icon_style = "color: #ffa726; font-size: 12px; font-weight: bold;"

                icon_label = QLabel(icon)
                icon_label.setStyleSheet(icon_style)
                icon_label.setFixedWidth(24)
                grid.addWidget(icon_label, row, 0)

                field_label = QLabel(comp.label)
                field_label.setStyleSheet(KEY_LABEL)
                grid.addWidget(field_label, row, 1)

                if comp.agreed and comp.consensus is not None:
                    val = self._format_xref_value(comp.field, comp.consensus)
                    val_label = QLabel(val)
                    val_label.setStyleSheet("color: #66bb6a; font-size: 12px;")
                    grid.addWidget(val_label, row, 2)
                else:
                    parts = []
                    for site, val in comp.values.items():
                        parts.append(f"{site}: {self._format_xref_value(comp.field, val)}")
                    val_label = QLabel("  |  ".join(parts))
                    val_label.setStyleSheet("color: #ffa726; font-size: 11px;")
                    val_label.setWordWrap(True)
                    grid.addWidget(val_label, row, 2)

                row += 1

            card.layout().addLayout(grid)

            summary = QLabel(f"{agreed_count}/{total} fields agree across all sources")
            color = "#66bb6a" if agreed_count == total else "#ffa726"
            summary.setStyleSheet(f"color: {color}; font-size: 12px; font-weight: bold; margin-top: 6px;")
            card.layout().addWidget(summary)

        self.cards_layout.addWidget(card)

    def _build_enrichment_card(self, listing: Listing):
        enrichment = self._enrichment
        searching = self._enrichment_searching

        card = self._make_card("Data Enrichment")

        if searching:
            search_label = QLabel("Searching other sites for data...")
            search_label.setStyleSheet("color: #81d4fa; font-size: 12px; font-style: italic;")
            card.layout().addWidget(search_label)

        if enrichment and enrichment.items:
            missing_items = [i for i in enrichment.items if i.current_value is None]
            differ_items = [i for i in enrichment.items if i.current_value is not None]

            if missing_items:
                hdr = QLabel(f"Found {len(missing_items)} missing field{'s' if len(missing_items) != 1 else ''}:")
                hdr.setStyleSheet("color: #66bb6a; font-size: 12px; font-weight: bold; margin-top: 4px;")
                card.layout().addWidget(hdr)

                grid = QGridLayout()
                grid.setColumnStretch(1, 1)
                grid.setHorizontalSpacing(12)
                grid.setVerticalSpacing(4)

                for row, item in enumerate(missing_items):
                    field_lbl = QLabel(item.label)
                    field_lbl.setStyleSheet(KEY_LABEL)
                    grid.addWidget(field_lbl, row, 0)

                    val_str = self._format_xref_value(item.field, item.found_value)
                    val_lbl = QLabel(f"{val_str}  ({item.source_site})")
                    val_lbl.setStyleSheet("color: #66bb6a; font-size: 12px;")
                    grid.addWidget(val_lbl, row, 1)

                card.layout().addLayout(grid)

            if differ_items:
                if missing_items:
                    sep = QFrame()
                    sep.setFrameShape(QFrame.Shape.HLine)
                    sep.setStyleSheet("color: #333; margin: 4px 0;")
                    card.layout().addWidget(sep)

                hdr2 = QLabel(f"{len(differ_items)} field{'s' if len(differ_items) != 1 else ''} differ:")
                hdr2.setStyleSheet("color: #ffa726; font-size: 12px; font-weight: bold; margin-top: 4px;")
                card.layout().addWidget(hdr2)

                grid2 = QGridLayout()
                grid2.setColumnStretch(1, 1)
                grid2.setHorizontalSpacing(12)
                grid2.setVerticalSpacing(4)

                for row, item in enumerate(differ_items):
                    field_lbl = QLabel(item.label)
                    field_lbl.setStyleSheet(KEY_LABEL)
                    grid2.addWidget(field_lbl, row, 0)

                    cur_str = self._format_xref_value(item.field, item.current_value)
                    new_str = self._format_xref_value(item.field, item.found_value)
                    diff_lbl = QLabel(f"{cur_str} -> {new_str}  ({item.source_site})")
                    diff_lbl.setStyleSheet("color: #ffa726; font-size: 12px;")
                    diff_lbl.setWordWrap(True)
                    grid2.addWidget(diff_lbl, row, 1)

                card.layout().addLayout(grid2)

            if not searching:
                btn_row = QHBoxLayout()

                if missing_items:
                    apply_missing_btn = QPushButton(f"Apply {len(missing_items)} Missing")
                    apply_missing_btn.setStyleSheet(
                        "QPushButton { background: #2d5a27; color: #fff; border: none; "
                        "border-radius: 4px; padding: 5px 14px; font-size: 12px; }"
                        "QPushButton:hover { background: #3a7a30; }"
                    )
                    fields = {i.field: i.found_value for i in missing_items}
                    apply_missing_btn.clicked.connect(
                        lambda checked=False, f=fields: self._apply_fields(f)
                    )
                    btn_row.addWidget(apply_missing_btn)

                if differ_items:
                    apply_all_btn = QPushButton(f"Apply All {len(enrichment.items)}")
                    apply_all_btn.setStyleSheet(
                        "QPushButton { background: #bf360c; color: #fff; border: none; "
                        "border-radius: 4px; padding: 5px 14px; font-size: 12px; }"
                        "QPushButton:hover { background: #d84315; }"
                    )
                    all_fields = {i.field: i.found_value for i in enrichment.items}
                    apply_all_btn.clicked.connect(
                        lambda checked=False, f=all_fields: self._apply_fields(f)
                    )
                    btn_row.addWidget(apply_all_btn)

                btn_row.addStretch()
                card.layout().addLayout(btn_row)

        elif enrichment and not enrichment.items and not searching:
            no_data = QLabel("No additional data found on other sites")
            no_data.setStyleSheet("color: #888; font-size: 12px; font-style: italic;")
            card.layout().addWidget(no_data)

        if enrichment and enrichment.sites_searched and not searching:
            sites_str = ", ".join(enrichment.sites_searched)
            searched_lbl = QLabel(f"Searched: {sites_str}")
            searched_lbl.setStyleSheet("color: #555; font-size: 10px; margin-top: 6px;")
            card.layout().addWidget(searched_lbl)

        self.cards_layout.addWidget(card)

    def _build_area_news_card(self, listing: Listing):
        card = self._make_card("Local Area News")
        layout = card.layout()

        if listing.id in self._area_news_searching:
            searching = QLabel("Searching local news for crime, fires and other incidents...")
            searching.setStyleSheet("color: #81d4fa; font-size: 12px; font-style: italic;")
            layout.addWidget(searching)

        news = self._area_news.get(listing.id)
        if news is not None and news.failed:
            failed = QLabel("Couldn't reach the news search. Check your connection and try again.")
            failed.setStyleSheet("color: #ffa726; font-size: 12px;")
            layout.addWidget(failed)
        elif news is not None:
            headings = {"street": "On {}", "neighbourhood": "In {}", "city": "Around {}"}
            windows = {"street": "past 5 years", "neighbourhood": "past 2 years", "city": "past year"}
            for scope in SCOPES:
                if scope not in news.scope_names:
                    continue
                heading = QLabel(f"{headings[scope].format(news.scope_names[scope])}  ·  {windows[scope]}")
                heading.setStyleSheet("color: #81d4fa; font-size: 12px; font-weight: bold; margin-top: 6px;")
                layout.addWidget(heading)

                incidents = news.for_scope(scope)
                if not incidents:
                    none = QLabel("No incident reports found.")
                    none.setStyleSheet("color: #888; font-size: 12px; padding-left: 8px;")
                    layout.addWidget(none)
                for incident in incidents:
                    when = (f"{incident.published:%b} {incident.published.day}, {incident.published.year}"
                            if incident.published else "Undated")
                    details = [when, incident.source] if incident.source else [when]
                    if incident.reports > 1:
                        details.append(f"{incident.reports} reports")
                    row = QLabel(
                        f'<span style="color:{AREA_NEWS_COLORS[incident.category]}; font-weight:bold;">'
                        f'{escape(CATEGORY_LABELS[incident.category])}</span>&nbsp;&nbsp;'
                        f'<a href="{escape(incident.url, quote=True)}" style="color:#e0e0e0;">'
                        f'{escape(incident.title)}</a>'
                        f'<span style="color:#888;">&nbsp;&nbsp;{escape(" · ".join(details))}</span>'
                    )
                    row.setTextFormat(Qt.TextFormat.RichText)
                    row.setWordWrap(True)
                    row.setStyleSheet("font-size: 12px; padding: 1px 0 1px 8px;")
                    row.linkActivated.connect(self.open_in_browser.emit)
                    layout.addWidget(row)

            caveat = QLabel(
                "Headlines from Google News, matched by keyword. Street results are articles that mention "
                "the street; neighbourhood results are headlines that name it. Open a headline to "
                "confirm it concerns this location."
            )
            caveat.setWordWrap(True)
            caveat.setStyleSheet("color: #666; font-size: 10px; margin-top: 6px;")
            layout.addWidget(caveat)

        self.cards_layout.addWidget(card)

    @staticmethod
    def _format_xref_value(field_name: str, value: object) -> str:
        if value is None:
            return "—"
        if field_name in ("list_price", "maintenance_fee", "taxes_annual"):
            try:
                return f"${float(value):,.0f}"
            except (ValueError, TypeError):
                return str(value)
        if field_name == "status":
            return str(value).replace("_", " ")
        return str(value)

    def _format_sqft(self, sqft_min, sqft_max):
        if sqft_min and sqft_max and sqft_min != sqft_max:
            return f"{sqft_min:,}–{sqft_max:,}"
        if sqft_min:
            return f"{sqft_min:,}"
        if sqft_max:
            return f"{sqft_max:,}"
        return "—"

    def _update_chart(self):
        if not self._snapshots:
            self._show_chart_message("No price history")
            return

        prices = [
            (s.fetched_at, s.list_price)
            for s in self._snapshots
            if s.list_price
        ]
        if len(prices) < 2:
            self._show_chart_message("Need 2+ data points for chart")
            return

        try:
            import matplotlib
            matplotlib.use("QtAgg")
            from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
            from matplotlib.figure import Figure
            from datetime import datetime
            import matplotlib.dates

            fig = Figure(figsize=(6, 2.5), dpi=100, facecolor="#1e1e1e")
            ax = fig.add_subplot(111)
            ax.set_facecolor("#1e1e1e")

            dates, vals = [], []
            for fetched_at, price in prices:
                if isinstance(fetched_at, str):
                    dates.append(datetime.fromisoformat(fetched_at))
                else:
                    dates.append(fetched_at)
                vals.append(price)

            ax.plot(dates, vals, marker="o", linewidth=2, markersize=5,
                    color="#42a5f5", markerfacecolor="#90caf9")
            ax.set_ylabel("Price ($)", color="#888")
            ax.tick_params(colors="#888")
            for spine in ax.spines.values():
                spine.set_color("#444")
            fig.autofmt_xdate()
            fig.tight_layout()

            canvas = FigureCanvasQTAgg(fig)
            old = self.chart_widget
            self.chart_layout.removeWidget(old)
            old.deleteLater()
            self.chart_widget = canvas
            self.chart_layout.addWidget(canvas)

        except ImportError:
            self._show_chart_message("Install matplotlib for charts")

    def _show_chart_message(self, text: str, color: str = "#666"):
        """Show a text placeholder where the chart goes, replacing a drawn chart if there is one."""
        if not isinstance(self.chart_widget, QLabel):
            old = self.chart_widget
            self.chart_layout.removeWidget(old)
            old.deleteLater()
            self.chart_widget = QLabel()
            self.chart_widget.setMinimumHeight(180)
            self.chart_widget.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.chart_layout.addWidget(self.chart_widget)
        self.chart_widget.setText(text)
        self.chart_widget.setStyleSheet(
            "background: #1e1e1e; border: 1px solid #333; border-radius: 8px; "
            f"color: {color}; font-size: 12px;"
        )

    def _save_notes(self):
        if self._listing:
            self.notes_saved.emit(self._listing.id, self.notes_edit.toPlainText())

    def _open_browser(self):
        if self._listing:
            self.open_in_browser.emit(self._listing.url)

    def _request_research(self):
        if self._listing:
            self.research_requested.emit(self._listing.id)

    def current_listing_id(self) -> Optional[int]:
        return self._listing.id if self._listing else None

    def set_research_running(self, running: bool):
        self.research_btn.setEnabled(not running)
        self.research_btn.setText("Researching..." if running else "Research More Details")
        # Research runs its own history fetch; a second one would collide with it
        self.fetch_history_btn.setEnabled(not running)

    def set_area_news(self, listing_id: int, news: Optional[AreaNews], searching: bool = False):
        if searching:
            self._area_news_searching.add(listing_id)
        else:
            self._area_news_searching.discard(listing_id)
        if news is not None:
            self._area_news[listing_id] = news
        if self._listing and self._listing.id == listing_id and self._snapshots:
            self._update_display()

    def _request_fetch_history(self):
        if self._listing:
            self.fetch_history_btn.setEnabled(False)
            self.fetch_history_btn.setText("Fetching...")
            self.fetch_history_requested.emit(self._listing.id)

    def set_history_fetch_done(self, found_new: bool = False):
        self.fetch_history_btn.setEnabled(True)
        self.fetch_history_btn.setText("Fetch History")
        # Leave an already-drawn chart alone; only the empty placeholder gets the notice
        if not found_new and isinstance(self.chart_widget, QLabel):
            self._show_chart_message("No additional price history found", "#ffa726")

    def _apply_fields(self, fields: dict):
        if self._listing:
            self.apply_enrichment_requested.emit(self._listing.id, fields)
