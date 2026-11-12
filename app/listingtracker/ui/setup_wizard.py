"""First-run setup wizard — collects what a search needs, one topic per page."""

from __future__ import annotations

import math
from html import escape

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QStackedWidget, QWidget,
    QLabel, QLineEdit, QDoubleSpinBox, QSpinBox, QCheckBox, QPushButton, QGroupBox,
)

from listingtracker.finance import (
    FinanceConfig, max_qualifying_price, monthly_carrying_cost, down_payment_required,
)
from listingtracker.profiles import BuyerProfile, PROPERTY_TYPE_CHOICES, profile_exists
from listingtracker.ui.location_editor import LocationEditor
from listingtracker.ui.profile_dialog import DIALOG_STYLE

STEPS = ["Welcome", "Finances", "Location", "Your home", "Review"]

# Typical Durham condo costs, used only to turn income into a rough price estimate
TYPICAL_MONTHLY_FEE = 400
TYPICAL_ANNUAL_TAX = 3_500

WIZARD_STYLE = DIALOG_STYLE + """
QLabel#wizardTitle { color: #fff; font-size: 20px; font-weight: bold; }
QLabel#wizardIntro { color: #aaa; font-size: 12px; }
QLabel#stepDone { color: #66bb6a; font-size: 12px; }
QLabel#stepCurrent { color: #81d4fa; font-size: 12px; font-weight: bold; }
QLabel#stepTodo { color: #666; font-size: 12px; }
QLabel#hint { color: #888; font-size: 11px; }
QLabel#error { color: #ef5350; font-size: 12px; font-weight: bold; }
QLabel#review { color: #ddd; font-size: 13px; }
"""


def _money_spin(maximum: float, step: float) -> QDoubleSpinBox:
    spin = QDoubleSpinBox()
    spin.setRange(0, maximum)
    spin.setDecimals(0)
    spin.setSingleStep(step)
    spin.setPrefix("$")
    spin.setGroupSeparatorShown(True)
    spin.setMinimumWidth(140)
    return spin


class SetupWizard(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.run_search = False
        self._qualify_estimate = 0.0
        self._monthly_estimate = 0.0
        self.setWindowTitle("Welcome to Durham Listing Tracker")
        self.setMinimumSize(900, 640)
        self.resize(960, 680)
        self.setStyleSheet(WIZARD_STYLE)
        self._build_ui()
        self._go(0)

    # --- Layout ---

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 14)
        layout.setSpacing(8)

        steps_row = QHBoxLayout()
        steps_row.setSpacing(6)
        self._step_labels: list[QLabel] = []
        for index, name in enumerate(STEPS):
            if index:
                arrow = QLabel("›")
                arrow.setObjectName("stepTodo")
                steps_row.addWidget(arrow)
            label = QLabel(f"{index + 1}. {name}")
            self._step_labels.append(label)
            steps_row.addWidget(label)
        steps_row.addStretch()
        layout.addLayout(steps_row)

        self.title_label = QLabel()
        self.title_label.setObjectName("wizardTitle")
        layout.addWidget(self.title_label)
        self.intro_label = QLabel()
        self.intro_label.setObjectName("wizardIntro")
        self.intro_label.setWordWrap(True)
        layout.addWidget(self.intro_label)

        self.stack = QStackedWidget()
        self._pages = [
            ("Let's set up your home search",
             "A few questions about your budget, where you want to live and the home you need. "
             "The tracker then builds your first list of matching listings.",
             self._build_welcome_page()),
            ("Your finances",
             "These numbers decide which listings you qualify for and what each one would cost you per month. "
             "They stay on this computer.",
             self._build_finance_page()),
            ("Where you live, work and want to be",
             "Search for an address or click the map. Home and work are optional, but with them every "
             "listing shows its commute.",
             self._build_location_page()),
            ("The home you need",
             "Listings that miss these are left out of your search or flagged.",
             self._build_home_page()),
            ("Ready to search",
             "Check everything looks right. You can change any of it later with the Edit button.",
             self._build_review_page()),
        ]
        self.stack.setObjectName("plainPanel")
        for _title, _intro, page in self._pages:
            page.setObjectName("plainPanel")
            self.stack.addWidget(page)
        layout.addWidget(self.stack, 1)

        self.error_label = QLabel()
        self.error_label.setObjectName("error")
        self.error_label.setWordWrap(True)
        layout.addWidget(self.error_label)

        nav = QHBoxLayout()
        self.later_btn = QPushButton("Set up later")
        self.later_btn.setAutoDefault(False)
        self.later_btn.clicked.connect(self.reject)
        nav.addWidget(self.later_btn)
        nav.addStretch()

        self.back_btn = QPushButton("Back")
        self.back_btn.setAutoDefault(False)
        self.back_btn.clicked.connect(lambda: self._go(self.stack.currentIndex() - 1))
        nav.addWidget(self.back_btn)

        self.next_btn = QPushButton("Next")
        self.next_btn.setObjectName("saveBtn")
        self.next_btn.clicked.connect(self._next)
        nav.addWidget(self.next_btn)

        self.finish_btn = QPushButton("Finish without searching")
        self.finish_btn.setAutoDefault(False)
        self.finish_btn.clicked.connect(lambda: self._finish(False))
        nav.addWidget(self.finish_btn)

        self.search_btn = QPushButton("Save && run my first search")
        self.search_btn.setObjectName("saveBtn")
        self.search_btn.clicked.connect(lambda: self._finish(True))
        nav.addWidget(self.search_btn)
        layout.addLayout(nav)

    def _build_welcome_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 12, 0, 0)

        group = QGroupBox("Your profile")
        form = QFormLayout()
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("e.g. Our first home")
        self.name_edit.setMaximumWidth(320)
        form.addRow("Profile name:", self.name_edit)
        self.buyer_edit = QLineEdit()
        self.buyer_edit.setPlaceholderText("Optional")
        self.buyer_edit.setMaximumWidth(320)
        form.addRow("Your name:", self.buyer_edit)
        group.setLayout(form)
        layout.addWidget(group)

        note = QLabel(
            "A profile holds one buyer's budget, locations and requirements. "
            "You can create more profiles later and switch between them."
        )
        note.setObjectName("hint")
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addStretch()
        return page

    def _build_finance_page(self) -> QWidget:
        page = QWidget()
        columns = QHBoxLayout(page)
        columns.setContentsMargins(0, 8, 0, 0)
        columns.setSpacing(16)

        left = QVBoxLayout()
        income_group = QGroupBox("Income && savings")
        income_form = QFormLayout()
        self.income = _money_spin(10_000_000, 1_000)
        income_form.addRow("Household income (per year):", self.income)
        self.down_payment = _money_spin(10_000_000, 1_000)
        income_form.addRow("Down payment saved:", self.down_payment)
        self.debt = _money_spin(100_000, 50)
        income_form.addRow("Monthly debt payments:", self.debt)
        self.first_time = QCheckBox("First-time buyer (land transfer tax rebate)")
        income_form.addRow(self.first_time)
        income_group.setLayout(income_form)
        left.addWidget(income_group)

        mortgage_group = QGroupBox("Mortgage")
        mortgage_form = QFormLayout()
        self.rate = QDoubleSpinBox()
        self.rate.setRange(0.1, 20.0)
        self.rate.setDecimals(2)
        self.rate.setSingleStep(0.05)
        self.rate.setSuffix(" %")
        self.rate.setValue(BuyerProfile.contract_rate * 100)
        mortgage_form.addRow("Interest rate:", self.rate)
        self.amort = QSpinBox()
        self.amort.setRange(5, 30)
        self.amort.setSuffix(" years")
        self.amort.setValue(BuyerProfile.amortization_years)
        mortgage_form.addRow("Amortization:", self.amort)
        mortgage_group.setLayout(mortgage_form)
        left.addWidget(mortgage_group)
        left.addStretch()
        columns.addLayout(left, 1)

        right = QVBoxLayout()
        budget_group = QGroupBox("Your budget")
        budget_layout = QVBoxLayout()

        price_form = QFormLayout()
        self.price_ceiling = _money_spin(10_000_000, 5_000)
        price_form.addRow("Most you'd pay for a home:", self.price_ceiling)
        budget_layout.addLayout(price_form)
        price_hint_row = QHBoxLayout()
        self.qualify_hint = QLabel()
        self.qualify_hint.setObjectName("hint")
        self.qualify_hint.setWordWrap(True)
        price_hint_row.addWidget(self.qualify_hint, 1)
        self.use_qualify_btn = QPushButton("Use this")
        self.use_qualify_btn.setAutoDefault(False)
        self.use_qualify_btn.clicked.connect(lambda: self.price_ceiling.setValue(self._qualify_estimate))
        price_hint_row.addWidget(self.use_qualify_btn, 0, Qt.AlignmentFlag.AlignTop)
        budget_layout.addLayout(price_hint_row)
        budget_layout.addSpacing(12)

        monthly_form = QFormLayout()
        self.monthly_ceiling = _money_spin(50_000, 50)
        monthly_form.addRow("Most you'd pay per month, all-in:", self.monthly_ceiling)
        budget_layout.addLayout(monthly_form)
        monthly_hint_row = QHBoxLayout()
        self.monthly_hint = QLabel()
        self.monthly_hint.setObjectName("hint")
        self.monthly_hint.setWordWrap(True)
        monthly_hint_row.addWidget(self.monthly_hint, 1)
        self.use_monthly_btn = QPushButton("Use this")
        self.use_monthly_btn.setAutoDefault(False)
        self.use_monthly_btn.clicked.connect(lambda: self.monthly_ceiling.setValue(self._monthly_estimate))
        monthly_hint_row.addWidget(self.use_monthly_btn, 0, Qt.AlignmentFlag.AlignTop)
        budget_layout.addLayout(monthly_hint_row)

        budget_group.setLayout(budget_layout)
        right.addWidget(budget_group)

        self.dp_warning = QLabel()
        self.dp_warning.setWordWrap(True)
        self.dp_warning.setStyleSheet("color: #ffa726; font-size: 11px; padding: 4px 0;")
        right.addWidget(self.dp_warning)

        right.addStretch()
        columns.addLayout(right, 1)

        for spin in (self.income, self.down_payment, self.debt, self.rate, self.amort, self.price_ceiling):
            spin.valueChanged.connect(self._update_finance_hints)
        self.monthly_ceiling.valueChanged.connect(self._update_finance_hints)
        self._update_finance_hints()
        return page

    def _build_location_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 8, 0, 0)
        self.location = LocationEditor(BuyerProfile())
        self.location.changed.connect(lambda: self.error_label.setText(""))
        layout.addWidget(self.location)
        return page

    def _build_home_page(self) -> QWidget:
        page = QWidget()
        columns = QHBoxLayout(page)
        columns.setContentsMargins(0, 8, 0, 0)
        columns.setSpacing(16)
        defaults = BuyerProfile()

        left = QVBoxLayout()
        type_group = QGroupBox("What kind of home?")
        type_layout = QVBoxLayout()
        self.type_checks: dict[str, QCheckBox] = {}
        for key, label in PROPERTY_TYPE_CHOICES:
            cb = QCheckBox(label)
            cb.setChecked(key in defaults.property_types)
            self.type_checks[key] = cb
            type_layout.addWidget(cb)
        type_group.setLayout(type_layout)
        left.addWidget(type_group)
        left.addStretch()
        columns.addLayout(left, 1)

        right = QVBoxLayout()
        need_group = QGroupBox("Must-haves")
        need_form = QFormLayout()
        self.min_beds = QSpinBox()
        self.min_beds.setRange(0, 10)
        self.min_beds.setValue(defaults.min_beds)
        need_form.addRow("Bedrooms (at least):", self.min_beds)
        self.min_baths = QSpinBox()
        self.min_baths.setRange(0, 10)
        self.min_baths.setValue(defaults.min_baths)
        need_form.addRow("Bathrooms (at least):", self.min_baths)
        self.min_sqft = QSpinBox()
        self.min_sqft.setRange(0, 50_000)
        self.min_sqft.setSingleStep(50)
        self.min_sqft.setSuffix(" sqft")
        self.min_sqft.setSpecialValueText("Any size")
        self.min_sqft.setValue(defaults.min_sqft)
        need_form.addRow("Size (at least):", self.min_sqft)
        self.parking_cb = QCheckBox("Parking")
        self.parking_cb.setChecked(defaults.parking_required)
        need_form.addRow(self.parking_cb)
        self.outdoor_cb = QCheckBox("Outdoor space (balcony, patio or yard)")
        self.outdoor_cb.setChecked(defaults.outdoor_space_required)
        need_form.addRow(self.outdoor_cb)
        self.laundry_cb = QCheckBox("In-suite laundry")
        self.laundry_cb.setChecked(defaults.laundry_insuite)
        need_form.addRow(self.laundry_cb)
        need_group.setLayout(need_form)
        right.addWidget(need_group)
        right.addStretch()
        columns.addLayout(right, 1)
        return page

    def _build_review_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 8, 0, 0)
        group = QGroupBox("Your search profile")
        inner = QVBoxLayout()
        self.review_label = QLabel()
        self.review_label.setObjectName("review")
        self.review_label.setWordWrap(True)
        self.review_label.setTextFormat(Qt.TextFormat.RichText)
        inner.addWidget(self.review_label)
        group.setLayout(inner)
        layout.addWidget(group)

        note = QLabel(
            "The first search checks every supported listing site for each of your cities and then "
            "loads the details of what it finds. It can take several minutes, and you can keep using "
            "the app while it runs."
        )
        note.setObjectName("hint")
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addStretch()
        return page

    # --- Finance helpers ---

    def _finance_config(self) -> FinanceConfig:
        return FinanceConfig(
            down_payment_available=self.down_payment.value(),
            annual_income=self.income.value(),
            monthly_debt_obligations=self.debt.value(),
            contract_rate=self.rate.value() / 100,
            amortization_years=self.amort.value(),
            first_time_buyer=self.first_time.isChecked(),
        )

    def _update_finance_hints(self):
        cfg = self._finance_config()
        if cfg.annual_income > 0:
            estimate = max_qualifying_price(TYPICAL_MONTHLY_FEE, TYPICAL_ANNUAL_TAX, "", cfg)
            self._qualify_estimate = math.floor(estimate / 1_000) * 1_000
            self.qualify_hint.setText(
                f"With these numbers a lender would likely approve up to about "
                f"${self._qualify_estimate:,.0f} (assuming ${TYPICAL_MONTHLY_FEE}/mo condo fees "
                f"and ${TYPICAL_ANNUAL_TAX:,}/yr property tax)."
            )
        else:
            self._qualify_estimate = 0.0
            self.qualify_hint.setText("Enter your income to see roughly what a lender would approve.")
        self.use_qualify_btn.setEnabled(self._qualify_estimate > 0)

        price = self.price_ceiling.value()
        if price > 0:
            monthly = monthly_carrying_cost(price, TYPICAL_MONTHLY_FEE, TYPICAL_ANNUAL_TAX, "", cfg)
            self._monthly_estimate = math.ceil(monthly / 50) * 50
            self.monthly_hint.setText(
                f"A ${price:,.0f} home would cost about ${self._monthly_estimate:,.0f} a month: "
                "mortgage, condo fees, property tax, utilities and insurance."
            )
        else:
            self._monthly_estimate = 0.0
            self.monthly_hint.setText("Set a maximum price to see what it would cost per month.")
        self.use_monthly_btn.setEnabled(self._monthly_estimate > 0)

        if price > 0:
            minimum = down_payment_required(price)
            if self.down_payment.value() < minimum:
                self.dp_warning.setText(
                    f"⚠ A ${price:,.0f} home needs at least ${minimum:,.0f} down. "
                    "You can still continue, but lenders will likely require a larger down payment."
                )
            else:
                self.dp_warning.setText("")
        else:
            self.dp_warning.setText("")

    # --- Navigation ---

    def _go(self, index: int):
        index = max(0, min(len(STEPS) - 1, index))
        last = index == len(STEPS) - 1
        if last:
            self._fill_review()
        self.stack.setCurrentIndex(index)
        title, intro, _page = self._pages[index]
        self.title_label.setText(title)
        self.intro_label.setText(intro)
        self.error_label.setText("")
        for i, label in enumerate(self._step_labels):
            label.setObjectName("stepDone" if i < index else "stepCurrent" if i == index else "stepTodo")
            label.style().unpolish(label)
            label.style().polish(label)
        self.back_btn.setVisible(index > 0)
        self.next_btn.setVisible(not last)
        self.finish_btn.setVisible(last)
        self.search_btn.setVisible(last)
        (self.search_btn if last else self.next_btn).setDefault(True)

    def _next(self):
        index = self.stack.currentIndex()
        problem = self._validate(index)
        if problem:
            self.error_label.setText(problem)
            return
        self._go(index + 1)

    def _validate(self, index: int) -> str:
        """Return what's missing on a page, or an empty string when it's complete."""
        if index == 0:
            name = self.name_edit.text().strip()
            if not name:
                return "Give your profile a name to continue."
            if profile_exists(name):
                return f"A profile named '{name}' already exists. Choose a different name."
        elif index == 1:
            if self.income.value() <= 0:
                return "Enter your household income."
            price = self.price_ceiling.value()
            if price <= 0:
                return "Set the most you'd pay for a home."
            if self.monthly_ceiling.value() <= 0:
                return "Set the most you'd pay per month."
        elif index == 2:
            if not self.location.cities():
                return "Add at least one city you'd live in."
        elif index == 3:
            if not any(cb.isChecked() for cb in self.type_checks.values()):
                return "Pick at least one kind of home."
        return ""

    def _finish(self, run_search: bool):
        for index in range(len(STEPS) - 1):
            problem = self._validate(index)
            if problem:
                self._go(index)
                self.error_label.setText(problem)
                return
        self.run_search = run_search
        self.accept()

    # --- Result ---

    def get_profile(self) -> BuyerProfile:
        profile = BuyerProfile(
            name=self.name_edit.text().strip(),
            buyer_name=self.buyer_edit.text().strip(),
            annual_income=self.income.value(),
            down_payment_available=self.down_payment.value(),
            down_payment_target=0,
            monthly_debt_obligations=self.debt.value(),
            contract_rate=round(self.rate.value() / 100, 6),
            amortization_years=self.amort.value(),
            first_time_buyer=self.first_time.isChecked(),
            price_target=self.price_ceiling.value(),
            price_ceiling=self.price_ceiling.value(),
            monthly_allin_ceiling=self.monthly_ceiling.value(),
            property_types=[key for key, cb in self.type_checks.items() if cb.isChecked()],
            min_beds=self.min_beds.value(),
            preferred_beds=self.min_beds.value(),
            min_baths=self.min_baths.value(),
            min_sqft=self.min_sqft.value(),
            preferred_sqft=self.min_sqft.value(),
            parking_required=self.parking_cb.isChecked(),
            outdoor_space_required=self.outdoor_cb.isChecked(),
            laundry_insuite=self.laundry_cb.isChecked(),
        )
        self.location.apply_to(profile)
        return profile

    def _fill_review(self):
        p = self.get_profile()
        type_labels = dict(PROPERTY_TYPE_CHOICES)
        needs = [f"{p.min_beds}+ bed", f"{p.min_baths}+ bath"]
        if p.min_sqft:
            needs.append(f"{p.min_sqft:,}+ sqft")
        needs += [text for flag, text in (
            (p.parking_required, "parking"),
            (p.outdoor_space_required, "outdoor space"),
            (p.laundry_insuite, "in-suite laundry"),
        ) if flag]

        rows = [
            ("Profile", p.name),
            ("Income", f"${p.annual_income:,.0f} a year"
                       + (" · first-time buyer" if p.first_time_buyer else "")),
            ("Down payment", f"${p.down_payment_available:,.0f}"),
            ("Mortgage", f"{p.contract_rate * 100:.2f}% over {p.amortization_years} years"
                         + (f" · ${p.monthly_debt_obligations:,.0f}/mo other debt"
                            if p.monthly_debt_obligations else "")),
            ("Budget", f"up to ${p.price_ceiling:,.0f} · up to ${p.monthly_allin_ceiling:,.0f} a month"),
            ("Lives at", p.home_address or "Not set"),
            ("Works at", p.work_address or "Not set"),
            ("Cities", ", ".join(c.title() for c in p.cities) or "None yet"),
            ("Looking for", ", ".join(type_labels.get(t, t) for t in p.property_types) or "Nothing selected"),
            ("Must-haves", ", ".join(needs)),
        ]
        html = "<table cellspacing='0' cellpadding='4'>" + "".join(
            f"<tr><td style='color:#888; padding-right:18px;'>{escape(key)}</td>"
            f"<td>{escape(value)}</td></tr>"
            for key, value in rows
        ) + "</table>"
        self.review_label.setText(html)
