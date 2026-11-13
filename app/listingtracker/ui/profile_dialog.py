"""Profile editor dialog — view/edit buyer profiles, dark theme."""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QTabWidget,
    QDoubleSpinBox, QSpinBox, QCheckBox, QPushButton, QLabel,
    QGroupBox, QLineEdit, QTextEdit, QWidget,
    QListWidget, QDateEdit, QMessageBox,
)
from PySide6.QtCore import QDate

from listingtracker.profiles import BuyerProfile, PROPERTY_TYPE_CHOICES, profile_exists
from listingtracker.ui.location_editor import LocationEditor

DIALOG_STYLE = """
QDialog {
    background: #1e1e1e;
    color: #e0e0e0;
}
QTabWidget::pane {
    background: #1e1e1e;
    border: 1px solid #333;
    border-top: none;
}
QTabBar::tab {
    background: #252525;
    color: #aaa;
    padding: 8px 16px;
    border: 1px solid #333;
    border-bottom: none;
    border-top-left-radius: 4px;
    border-top-right-radius: 4px;
    margin-right: 2px;
}
QTabBar::tab:selected {
    background: #1e1e1e;
    color: #81d4fa;
    border-bottom: 2px solid #42a5f5;
}
QGroupBox {
    background: #252525;
    border: 1px solid #333;
    border-radius: 6px;
    margin-top: 12px;
    padding: 10px 8px 8px 8px;
    font-weight: bold;
    color: #ccc;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 4px;
    color: #81d4fa;
}
QLabel { color: #ccc; font-size: 12px; background: transparent; }
QWidget#plainPanel { background: transparent; }
QMenu {
    background: #2a2a2a;
    color: #e0e0e0;
    border: 1px solid #555;
}
QMenu::item { padding: 5px 18px; }
QMenu::item:selected { background: #1565c0; }
QLineEdit, QTextEdit {
    background: #2a2a2a;
    color: #e0e0e0;
    border: 1px solid #444;
    border-radius: 4px;
    padding: 4px 6px;
    font-size: 12px;
}
QLineEdit:focus, QTextEdit:focus {
    border-color: #42a5f5;
}
QDoubleSpinBox, QSpinBox, QDateEdit {
    background: #2a2a2a;
    color: #e0e0e0;
    border: 1px solid #444;
    border-radius: 4px;
    padding: 3px 6px;
    font-size: 12px;
    min-width: 100px;
}
QDateEdit::drop-down {
    subcontrol-origin: padding;
    subcontrol-position: center right;
    width: 20px;
    border-left: 1px solid #444;
}
QCheckBox {
    color: #ccc;
    font-size: 12px;
    background: transparent;
}
QCheckBox::indicator {
    width: 14px; height: 14px;
    border: 1px solid #555;
    border-radius: 3px;
    background: #2a2a2a;
}
QCheckBox::indicator:checked {
    background: #42a5f5;
    border-color: #42a5f5;
}
QListWidget {
    background: #2a2a2a;
    color: #ddd;
    border: 1px solid #444;
    border-radius: 4px;
    font-size: 12px;
}
QPushButton {
    background: #2a2a2a;
    color: #e0e0e0;
    border: 1px solid #444;
    border-radius: 4px;
    padding: 5px 14px;
    font-size: 12px;
}
QPushButton:hover {
    background: #383838;
}
QPushButton#saveBtn {
    background: #1565c0;
    color: #fff;
    border: none;
    font-weight: bold;
}
QPushButton#saveBtn:hover {
    background: #1976d2;
}
QPushButton#deleteItemBtn {
    background: #c62828;
    color: #fff;
    border: none;
    padding: 3px 8px;
}
"""


class ProfileDialog(QDialog):
    def __init__(self, profile: BuyerProfile, parent=None, new: bool = False):
        super().__init__(parent)
        self.profile = profile
        self._new = new
        self.setWindowTitle("Edit Profile" if not new else "New Profile")
        self.setMinimumSize(860, 680)
        self.setStyleSheet(DIALOG_STYLE)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # Profile name
        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("Profile Name:"))
        self.name_edit = QLineEdit(self.profile.name)
        self.name_edit.setMaximumWidth(300)
        name_row.addWidget(self.name_edit)
        name_row.addStretch()
        layout.addLayout(name_row)

        tabs = QTabWidget()

        # --- Tab 1: Financial ---
        fin_tab = QWidget()
        fin_layout = QVBoxLayout(fin_tab)

        fin_group = QGroupBox("Income && Down Payment")
        fin_form = QFormLayout()

        self.income = QDoubleSpinBox()
        self.income.setRange(0, 10_000_000)
        self.income.setDecimals(0)
        self.income.setPrefix("$")
        self.income.setValue(self.profile.annual_income)
        fin_form.addRow("Annual income:", self.income)

        self.dp_available = QDoubleSpinBox()
        self.dp_available.setRange(0, 10_000_000)
        self.dp_available.setDecimals(0)
        self.dp_available.setPrefix("$")
        self.dp_available.setValue(self.profile.down_payment_available)
        fin_form.addRow("Down payment (max):", self.dp_available)

        self.dp_target = QDoubleSpinBox()
        self.dp_target.setRange(0, 10_000_000)
        self.dp_target.setDecimals(0)
        self.dp_target.setPrefix("$")
        self.dp_target.setValue(self.profile.down_payment_target)
        fin_form.addRow("Down payment (min):", self.dp_target)

        self.liquid_reserve = QDoubleSpinBox()
        self.liquid_reserve.setRange(0, 1_000_000)
        self.liquid_reserve.setDecimals(0)
        self.liquid_reserve.setPrefix("$")
        self.liquid_reserve.setValue(self.profile.liquid_reserve)
        fin_form.addRow("Liquid reserve:", self.liquid_reserve)

        self.debt = QDoubleSpinBox()
        self.debt.setRange(0, 100_000)
        self.debt.setDecimals(0)
        self.debt.setPrefix("$")
        self.debt.setValue(self.profile.monthly_debt_obligations)
        fin_form.addRow("Monthly debt (lender-visible):", self.debt)

        self.real_expenses = QDoubleSpinBox()
        self.real_expenses.setRange(0, 50_000)
        self.real_expenses.setDecimals(0)
        self.real_expenses.setPrefix("$")
        self.real_expenses.setValue(self.profile.real_monthly_expenses)
        fin_form.addRow("Real monthly expenses (not lender):", self.real_expenses)

        self.rate = QDoubleSpinBox()
        self.rate.setRange(0.001, 0.20)
        self.rate.setDecimals(4)
        self.rate.setSingleStep(0.0025)
        self.rate.setValue(self.profile.contract_rate)
        fin_form.addRow("Contract rate:", self.rate)

        self.amort = QSpinBox()
        self.amort.setRange(5, 30)
        self.amort.setValue(self.profile.amortization_years)
        fin_form.addRow("Amortization (years):", self.amort)

        self.gds = QDoubleSpinBox()
        self.gds.setRange(0.01, 1.0)
        self.gds.setDecimals(2)
        self.gds.setSingleStep(0.01)
        self.gds.setValue(self.profile.gds_limit)
        fin_form.addRow("GDS limit:", self.gds)

        self.tds = QDoubleSpinBox()
        self.tds.setRange(0.01, 1.0)
        self.tds.setDecimals(2)
        self.tds.setSingleStep(0.01)
        self.tds.setValue(self.profile.tds_limit)
        fin_form.addRow("TDS limit:", self.tds)

        self.ftb = QCheckBox("First-time buyer (LTT rebate)")
        self.ftb.setChecked(self.profile.first_time_buyer)
        fin_form.addRow(self.ftb)

        fin_group.setLayout(fin_form)
        fin_layout.addWidget(fin_group)

        util_group = QGroupBox("Utility Estimates")
        util_form = QFormLayout()
        self.heat = QDoubleSpinBox()
        self.heat.setRange(0, 1000)
        self.heat.setDecimals(0)
        self.heat.setPrefix("$")
        self.heat.setValue(self.profile.lender_heat_estimate)
        util_form.addRow("Heat estimate:", self.heat)

        self.hydro = QDoubleSpinBox()
        self.hydro.setRange(0, 1000)
        self.hydro.setDecimals(0)
        self.hydro.setPrefix("$")
        self.hydro.setValue(self.profile.hydro_estimate)
        util_form.addRow("Hydro estimate:", self.hydro)

        self.ins = QDoubleSpinBox()
        self.ins.setRange(0, 1000)
        self.ins.setDecimals(0)
        self.ins.setPrefix("$")
        self.ins.setValue(self.profile.insurance_estimate)
        util_form.addRow("Insurance estimate:", self.ins)
        util_group.setLayout(util_form)
        fin_layout.addWidget(util_group)
        fin_layout.addStretch()
        tabs.addTab(fin_tab, "Financial")

        # --- Tab 2: Budget ---
        budget_tab = QWidget()
        budget_layout = QVBoxLayout(budget_tab)

        gen_group = QGroupBox("General")
        gen_form = QFormLayout()

        self.price_target = QDoubleSpinBox()
        self.price_target.setRange(0, 10_000_000)
        self.price_target.setDecimals(0)
        self.price_target.setPrefix("$")
        self.price_target.setValue(self.profile.price_target)
        gen_form.addRow("Price target:", self.price_target)

        self.price_ceiling = QDoubleSpinBox()
        self.price_ceiling.setRange(0, 10_000_000)
        self.price_ceiling.setDecimals(0)
        self.price_ceiling.setPrefix("$")
        self.price_ceiling.setValue(self.profile.price_ceiling)
        gen_form.addRow("Absolute ceiling:", self.price_ceiling)

        self.monthly_ceil = QDoubleSpinBox()
        self.monthly_ceil.setRange(0, 50_000)
        self.monthly_ceil.setDecimals(0)
        self.monthly_ceil.setPrefix("$")
        self.monthly_ceil.setValue(self.profile.monthly_allin_ceiling)
        gen_form.addRow("Monthly all-in ceiling:", self.monthly_ceil)

        self.monthly_target = QDoubleSpinBox()
        self.monthly_target.setRange(0, 50_000)
        self.monthly_target.setDecimals(0)
        self.monthly_target.setPrefix("$")
        self.monthly_target.setSpecialValueText("Not set")
        self.monthly_target.setValue(self.profile.monthly_allin_target)
        self.monthly_target.setToolTip(
            "Goal monthly cost — shows how to adjust down payment to reach it"
        )
        gen_form.addRow("Monthly all-in target:", self.monthly_target)

        gen_group.setLayout(gen_form)
        budget_layout.addWidget(gen_group)

        condo_group = QGroupBox("Condo / Apartment")
        condo_form = QFormLayout()

        self.max_maint_fee = QDoubleSpinBox()
        self.max_maint_fee.setRange(0, 10_000)
        self.max_maint_fee.setDecimals(0)
        self.max_maint_fee.setPrefix("$")
        self.max_maint_fee.setSpecialValueText("No limit")
        self.max_maint_fee.setValue(self.profile.max_maintenance_fee)
        condo_form.addRow("Max maintenance fee ($/mo):", self.max_maint_fee)

        self.fee_warn = QDoubleSpinBox()
        self.fee_warn.setRange(0, 5.0)
        self.fee_warn.setDecimals(2)
        self.fee_warn.setSingleStep(0.05)
        self.fee_warn.setPrefix("$")
        self.fee_warn.setValue(self.profile.fee_per_sqft_warn)
        condo_form.addRow("Fee/sqft warning:", self.fee_warn)

        self.fee_reject = QDoubleSpinBox()
        self.fee_reject.setRange(0, 5.0)
        self.fee_reject.setDecimals(2)
        self.fee_reject.setSingleStep(0.05)
        self.fee_reject.setPrefix("$")
        self.fee_reject.setValue(self.profile.fee_per_sqft_reject)
        condo_form.addRow("Fee/sqft reject:", self.fee_reject)

        self.condo_max_ppsf = QDoubleSpinBox()
        self.condo_max_ppsf.setRange(0, 5000)
        self.condo_max_ppsf.setDecimals(0)
        self.condo_max_ppsf.setPrefix("$")
        self.condo_max_ppsf.setSpecialValueText("No limit")
        self.condo_max_ppsf.setValue(self.profile.condo_max_price_per_sqft)
        condo_form.addRow("Max price/sqft:", self.condo_max_ppsf)

        condo_group.setLayout(condo_form)
        budget_layout.addWidget(condo_group)

        freehold_group = QGroupBox("Freehold")
        freehold_form = QFormLayout()

        self.max_taxes = QDoubleSpinBox()
        self.max_taxes.setRange(0, 100_000)
        self.max_taxes.setDecimals(0)
        self.max_taxes.setPrefix("$")
        self.max_taxes.setSpecialValueText("No limit")
        self.max_taxes.setValue(self.profile.max_taxes_annual)
        freehold_form.addRow("Max annual taxes:", self.max_taxes)

        self.freehold_max_ppsf = QDoubleSpinBox()
        self.freehold_max_ppsf.setRange(0, 5000)
        self.freehold_max_ppsf.setDecimals(0)
        self.freehold_max_ppsf.setPrefix("$")
        self.freehold_max_ppsf.setSpecialValueText("No limit")
        self.freehold_max_ppsf.setValue(self.profile.freehold_max_price_per_sqft)
        freehold_form.addRow("Max price/sqft:", self.freehold_max_ppsf)

        freehold_group.setLayout(freehold_form)
        budget_layout.addWidget(freehold_group)
        budget_layout.addStretch()
        tabs.addTab(budget_tab, "Budget")

        # --- Tab 3: Location ---
        loc_tab = QWidget()
        loc_layout = QVBoxLayout(loc_tab)

        self.location_editor = LocationEditor(self.profile)
        loc_layout.addWidget(self.location_editor, 1)

        prox_group = QGroupBox("Proximity (places you want nearby)")
        prox_inner = QVBoxLayout()

        self.poi_list = QListWidget()
        self.poi_list.setMaximumHeight(60)
        for poi in self.profile.proximity_pois:
            self.poi_list.addItem(poi)
        prox_inner.addWidget(self.poi_list)

        poi_btns = QHBoxLayout()
        self.poi_input = QLineEdit()
        self.poi_input.setPlaceholderText("e.g. Whitby GO Station, nearest pharmacy...")
        self.poi_input.returnPressed.connect(self._add_poi)
        poi_btns.addWidget(self.poi_input)
        add_poi = QPushButton("Add")
        add_poi.clicked.connect(self._add_poi)
        poi_btns.addWidget(add_poi)
        rm_poi = QPushButton("Remove")
        rm_poi.setObjectName("deleteItemBtn")
        rm_poi.clicked.connect(self._remove_poi)
        poi_btns.addWidget(rm_poi)
        prox_inner.addLayout(poi_btns)
        prox_group.setLayout(prox_inner)
        loc_layout.addWidget(prox_group)
        tabs.addTab(loc_tab, "Location")

        # --- Tab 3: Requirements ---
        req_tab = QWidget()
        req_layout = QVBoxLayout(req_tab)

        type_group = QGroupBox("Property Types to Search For")
        type_inner = QVBoxLayout()
        self.type_checks: dict[str, QCheckBox] = {}
        for key, label in PROPERTY_TYPE_CHOICES:
            cb = QCheckBox(label)
            cb.setChecked(key in self.profile.property_types)
            self.type_checks[key] = cb
            type_inner.addWidget(cb)
        type_group.setLayout(type_inner)
        req_layout.addWidget(type_group)

        prop_group = QGroupBox("Property Requirements")
        prop_form = QFormLayout()

        self.min_beds = QSpinBox()
        self.min_beds.setRange(0, 10)
        self.min_beds.setValue(self.profile.min_beds)
        prop_form.addRow("Min bedrooms:", self.min_beds)

        self.pref_beds = QSpinBox()
        self.pref_beds.setRange(0, 10)
        self.pref_beds.setValue(self.profile.preferred_beds)
        prop_form.addRow("Preferred bedrooms:", self.pref_beds)

        self.min_baths = QSpinBox()
        self.min_baths.setRange(0, 10)
        self.min_baths.setValue(self.profile.min_baths)
        prop_form.addRow("Min bathrooms:", self.min_baths)

        self.min_sqft = QSpinBox()
        self.min_sqft.setRange(0, 50_000)
        self.min_sqft.setValue(self.profile.min_sqft)
        prop_form.addRow("Min sqft:", self.min_sqft)

        self.pref_sqft = QSpinBox()
        self.pref_sqft.setRange(0, 50_000)
        self.pref_sqft.setValue(self.profile.preferred_sqft)
        prop_form.addRow("Preferred sqft:", self.pref_sqft)

        self.parking_cb = QCheckBox("Parking required")
        self.parking_cb.setChecked(self.profile.parking_required)
        prop_form.addRow(self.parking_cb)

        self.outdoor_cb = QCheckBox("Outdoor space required (balcony/patio/yard)")
        self.outdoor_cb.setChecked(self.profile.outdoor_space_required)
        prop_form.addRow(self.outdoor_cb)

        self.laundry_cb = QCheckBox("In-suite laundry")
        self.laundry_cb.setChecked(self.profile.laundry_insuite)
        prop_form.addRow(self.laundry_cb)

        self.pets_edit = QLineEdit(self.profile.pets_description)
        self.pets_edit.setPlaceholderText("e.g., Large-breed dog (Rhodesian Ridgeback mix)")
        prop_form.addRow("Pets:", self.pets_edit)

        prop_group.setLayout(prop_form)
        req_layout.addWidget(prop_group)
        req_layout.addStretch()
        tabs.addTab(req_tab, "Requirements")

        # --- Tab 4: Buffer / Watch ---
        buf_tab = QWidget()
        buf_layout = QVBoxLayout(buf_tab)

        buf_group = QGroupBox("Buffer Settings (show listings just outside limits)")
        buf_form = QFormLayout()

        self.show_buffer = QCheckBox("Show buffer listings")
        self.show_buffer.setChecked(self.profile.show_buffer_listings)
        buf_form.addRow(self.show_buffer)

        self.price_buf = QDoubleSpinBox()
        self.price_buf.setRange(0, 50)
        self.price_buf.setDecimals(1)
        self.price_buf.setSuffix("%")
        self.price_buf.setValue(self.profile.price_buffer_pct)
        buf_form.addRow("Price buffer:", self.price_buf)

        self.monthly_buf = QDoubleSpinBox()
        self.monthly_buf.setRange(0, 50)
        self.monthly_buf.setDecimals(1)
        self.monthly_buf.setSuffix("%")
        self.monthly_buf.setValue(self.profile.monthly_buffer_pct)
        buf_form.addRow("Monthly cost buffer:", self.monthly_buf)

        self.fee_buf = QDoubleSpinBox()
        self.fee_buf.setRange(0, 50)
        self.fee_buf.setDecimals(1)
        self.fee_buf.setSuffix("%")
        self.fee_buf.setValue(self.profile.fee_buffer_pct)
        buf_form.addRow("Fee/sqft buffer:", self.fee_buf)

        buf_group.setLayout(buf_form)
        buf_layout.addWidget(buf_group)

        info = QLabel(
            "Buffer listings appear with an amber WATCHING tag in the sidebar.\n"
            "They're outside your hard limits but close enough to watch for price drops."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color: #888; font-size: 11px; padding: 8px;")
        buf_layout.addWidget(info)
        buf_layout.addStretch()
        tabs.addTab(buf_tab, "Buffer / Watch")

        # --- Tab 5: Deal Breakers ---
        db_tab = QWidget()
        db_layout = QVBoxLayout(db_tab)

        db_group = QGroupBox("Deal Breakers")
        db_inner = QVBoxLayout()
        self.breakers_list = QListWidget()
        self.breakers_list.setMaximumHeight(200)
        for item in self.profile.deal_breakers:
            self.breakers_list.addItem(item)
        db_inner.addWidget(self.breakers_list)

        db_btns = QHBoxLayout()
        self.breaker_input = QLineEdit()
        self.breaker_input.setPlaceholderText("Add a deal-breaker...")
        db_btns.addWidget(self.breaker_input)
        add_db = QPushButton("Add")
        add_db.clicked.connect(self._add_breaker)
        db_btns.addWidget(add_db)
        rm_db = QPushButton("Remove")
        rm_db.setObjectName("deleteItemBtn")
        rm_db.clicked.connect(self._remove_breaker)
        db_btns.addWidget(rm_db)
        db_inner.addLayout(db_btns)
        db_group.setLayout(db_inner)
        db_layout.addWidget(db_group)

        dd_group = QGroupBox("Due Diligence Flags")
        dd_inner = QVBoxLayout()
        self.dd_list = QListWidget()
        self.dd_list.setMaximumHeight(200)
        for item in self.profile.due_diligence:
            self.dd_list.addItem(item)
        dd_inner.addWidget(self.dd_list)

        dd_btns = QHBoxLayout()
        self.dd_input = QLineEdit()
        self.dd_input.setPlaceholderText("Add a due-diligence item...")
        dd_btns.addWidget(self.dd_input)
        add_dd = QPushButton("Add")
        add_dd.clicked.connect(self._add_dd)
        dd_btns.addWidget(add_dd)
        rm_dd = QPushButton("Remove")
        rm_dd.setObjectName("deleteItemBtn")
        rm_dd.clicked.connect(self._remove_dd)
        dd_btns.addWidget(rm_dd)
        dd_inner.addLayout(dd_btns)
        dd_group.setLayout(dd_inner)
        db_layout.addWidget(dd_group)
        db_layout.addStretch()
        tabs.addTab(db_tab, "Deal Breakers")

        # --- Tab 6: Timeline ---
        tl_tab = QWidget()
        tl_layout = QVBoxLayout(tl_tab)

        dates_group = QGroupBox("Key Dates")
        dates_form = QFormLayout()

        self.close_by_cb = QCheckBox()
        self.close_by_date = QDateEdit()
        self.close_by_date.setCalendarPopup(True)
        self.close_by_date.setDisplayFormat("MMMM d, yyyy")
        close_by_row = QHBoxLayout()
        close_by_row.addWidget(self.close_by_cb)
        close_by_row.addWidget(self.close_by_date, 1)
        if self.profile.close_by_date:
            self.close_by_cb.setChecked(True)
            self.close_by_date.setDate(QDate.fromString(self.profile.close_by_date, "yyyy-MM-dd"))
        else:
            self.close_by_cb.setChecked(False)
            self.close_by_date.setDate(QDate.currentDate().addMonths(3))
            self.close_by_date.setEnabled(False)
        self.close_by_cb.toggled.connect(self.close_by_date.setEnabled)
        dates_form.addRow("Must close by:", close_by_row)

        self.earliest_offer_cb = QCheckBox()
        self.earliest_offer_date = QDateEdit()
        self.earliest_offer_date.setCalendarPopup(True)
        self.earliest_offer_date.setDisplayFormat("MMMM d, yyyy")
        earliest_row = QHBoxLayout()
        earliest_row.addWidget(self.earliest_offer_cb)
        earliest_row.addWidget(self.earliest_offer_date, 1)
        if self.profile.earliest_offer_date:
            self.earliest_offer_cb.setChecked(True)
            self.earliest_offer_date.setDate(QDate.fromString(self.profile.earliest_offer_date, "yyyy-MM-dd"))
        else:
            self.earliest_offer_cb.setChecked(False)
            self.earliest_offer_date.setDate(QDate.currentDate())
            self.earliest_offer_date.setEnabled(False)
        self.earliest_offer_cb.toggled.connect(self.earliest_offer_date.setEnabled)
        dates_form.addRow("Earliest offer:", earliest_row)

        self.latest_offer_cb = QCheckBox()
        self.latest_offer_date = QDateEdit()
        self.latest_offer_date.setCalendarPopup(True)
        self.latest_offer_date.setDisplayFormat("MMMM d, yyyy")
        latest_row = QHBoxLayout()
        latest_row.addWidget(self.latest_offer_cb)
        latest_row.addWidget(self.latest_offer_date, 1)
        if self.profile.latest_offer_date:
            self.latest_offer_cb.setChecked(True)
            self.latest_offer_date.setDate(QDate.fromString(self.profile.latest_offer_date, "yyyy-MM-dd"))
        else:
            self.latest_offer_cb.setChecked(False)
            self.latest_offer_date.setDate(QDate.currentDate().addMonths(1))
            self.latest_offer_date.setEnabled(False)
        self.latest_offer_cb.toggled.connect(self.latest_offer_date.setEnabled)
        dates_form.addRow("Latest offer:", latest_row)

        dates_group.setLayout(dates_form)
        tl_layout.addWidget(dates_group)

        risk_group = QGroupBox("Risk Notes")
        risk_inner = QVBoxLayout()
        self.risk_notes_edit = QTextEdit()
        self.risk_notes_edit.setMaximumHeight(80)
        self.risk_notes_edit.setPlainText(self.profile.risk_notes)
        risk_inner.addWidget(self.risk_notes_edit)
        risk_group.setLayout(risk_inner)
        tl_layout.addWidget(risk_group)

        id_group = QGroupBox("Buyer Info")
        id_form = QFormLayout()
        self.buyer_name_edit = QLineEdit(self.profile.buyer_name)
        id_form.addRow("Name:", self.buyer_name_edit)
        self.employer_edit = QLineEdit(self.profile.employer)
        id_form.addRow("Employer:", self.employer_edit)
        self.emp_start_edit = QLineEdit(self.profile.employment_start)
        id_form.addRow("Employment start:", self.emp_start_edit)
        id_group.setLayout(id_form)
        tl_layout.addWidget(id_group)

        notes_group = QGroupBox("Notes")
        notes_inner = QVBoxLayout()
        self.notes_edit = QTextEdit()
        self.notes_edit.setMaximumHeight(120)
        self.notes_edit.setPlainText(self.profile.notes)
        notes_inner.addWidget(self.notes_edit)
        notes_group.setLayout(notes_inner)
        tl_layout.addWidget(notes_group)
        tl_layout.addStretch()
        tabs.addTab(tl_tab, "Timeline")

        layout.addWidget(tabs)

        # Buttons
        buttons = QHBoxLayout()
        save_btn = QPushButton("Save Profile")
        save_btn.setObjectName("saveBtn")
        save_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        buttons.addStretch()
        buttons.addWidget(save_btn)
        buttons.addWidget(cancel_btn)
        layout.addLayout(buttons)

    def accept(self):
        name = self.name_edit.text().strip() or "Default"
        original = self.profile.name
        if (self._new or name != original) and profile_exists(name):
            QMessageBox.warning(
                self, "Duplicate Name",
                f'A profile named "{name}" already exists. Choose a different name.',
            )
            self.name_edit.setFocus()
            return
        super().accept()

    def _add_poi(self):
        text = self.poi_input.text().strip()
        if text:
            existing = [self.poi_list.item(i).text().lower()
                        for i in range(self.poi_list.count())]
            if text.lower() not in existing:
                self.poi_list.addItem(text)
            self.poi_input.clear()

    def _remove_poi(self):
        row = self.poi_list.currentRow()
        if row >= 0:
            self.poi_list.takeItem(row)

    def _add_breaker(self):
        text = self.breaker_input.text().strip()
        if text:
            self.breakers_list.addItem(text)
            self.breaker_input.clear()

    def _remove_breaker(self):
        row = self.breakers_list.currentRow()
        if row >= 0:
            self.breakers_list.takeItem(row)

    def _add_dd(self):
        text = self.dd_input.text().strip()
        if text:
            self.dd_list.addItem(text)
            self.dd_input.clear()

    def _remove_dd(self):
        row = self.dd_list.currentRow()
        if row >= 0:
            self.dd_list.takeItem(row)

    def get_profile(self) -> BuyerProfile:
        pois = [self.poi_list.item(i).text() for i in range(self.poi_list.count())]
        breakers = [self.breakers_list.item(i).text() for i in range(self.breakers_list.count())]
        dd = [self.dd_list.item(i).text() for i in range(self.dd_list.count())]

        profile = replace(
            self.profile,
            name=self.name_edit.text().strip() or "Default",
            buyer_name=self.buyer_name_edit.text().strip(),
            employer=self.employer_edit.text().strip(),
            employment_start=self.emp_start_edit.text().strip(),
            notes=self.notes_edit.toPlainText(),
            annual_income=self.income.value(),
            down_payment_available=self.dp_available.value(),
            down_payment_target=self.dp_target.value(),
            liquid_reserve=self.liquid_reserve.value(),
            monthly_debt_obligations=self.debt.value(),
            real_monthly_expenses=self.real_expenses.value(),
            contract_rate=self.rate.value(),
            amortization_years=self.amort.value(),
            gds_limit=self.gds.value(),
            tds_limit=self.tds.value(),
            first_time_buyer=self.ftb.isChecked(),
            lender_heat_estimate=self.heat.value(),
            hydro_estimate=self.hydro.value(),
            insurance_estimate=self.ins.value(),
            price_target=self.price_target.value(),
            price_ceiling=self.price_ceiling.value(),
            monthly_allin_ceiling=self.monthly_ceil.value(),
            monthly_allin_target=self.monthly_target.value(),
            max_maintenance_fee=self.max_maint_fee.value(),
            condo_max_price_per_sqft=self.condo_max_ppsf.value(),
            max_taxes_annual=self.max_taxes.value(),
            freehold_max_price_per_sqft=self.freehold_max_ppsf.value(),
            proximity_pois=pois,
            property_types=[key for key, cb in self.type_checks.items() if cb.isChecked()],
            min_beds=self.min_beds.value(),
            preferred_beds=self.pref_beds.value(),
            min_baths=self.min_baths.value(),
            min_sqft=self.min_sqft.value(),
            preferred_sqft=self.pref_sqft.value(),
            parking_required=self.parking_cb.isChecked(),
            outdoor_space_required=self.outdoor_cb.isChecked(),
            laundry_insuite=self.laundry_cb.isChecked(),
            pets_description=self.pets_edit.text().strip(),
            fee_per_sqft_warn=self.fee_warn.value(),
            fee_per_sqft_reject=self.fee_reject.value(),
            price_buffer_pct=self.price_buf.value(),
            monthly_buffer_pct=self.monthly_buf.value(),
            fee_buffer_pct=self.fee_buf.value(),
            show_buffer_listings=self.show_buffer.isChecked(),
            deal_breakers=breakers,
            due_diligence=dd,
            close_by_date=self.close_by_date.date().toString("yyyy-MM-dd") if self.close_by_cb.isChecked() else "",
            earliest_offer_date=self.earliest_offer_date.date().toString("yyyy-MM-dd") if self.earliest_offer_cb.isChecked() else "",
            latest_offer_date=self.latest_offer_date.date().toString("yyyy-MM-dd") if self.latest_offer_cb.isChecked() else "",
            risk_notes=self.risk_notes_edit.toPlainText(),
        )
        self.location_editor.apply_to(profile)
        return profile
