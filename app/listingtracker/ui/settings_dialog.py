"""Settings dialog for financial parameters — dark theme."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QDoubleSpinBox, QSpinBox, QCheckBox, QPushButton, QLabel, QGroupBox,
)
from PySide6.QtCore import Qt

from listingtracker.finance import FinanceConfig

_FC = FinanceConfig()

SETTINGS_STYLE = """
QDialog {
    background: #1e1e1e;
    color: #e0e0e0;
}
QGroupBox {
    background: #252525;
    border: 1px solid #333;
    border-radius: 8px;
    margin-top: 14px;
    padding: 14px 10px 10px 10px;
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
QLabel {
    color: #ccc;
    font-size: 12px;
}
QDoubleSpinBox, QSpinBox {
    background: #2a2a2a;
    color: #e0e0e0;
    border: 1px solid #444;
    border-radius: 4px;
    padding: 3px 6px;
    font-size: 12px;
    min-width: 120px;
}
QDoubleSpinBox:focus, QSpinBox:focus {
    border-color: #42a5f5;
}
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
QPushButton {
    background: #2a2a2a;
    color: #e0e0e0;
    border: 1px solid #444;
    border-radius: 4px;
    padding: 6px 16px;
    font-size: 12px;
}
QPushButton:hover {
    background: #383838;
    border-color: #555;
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
"""


class SettingsDialog(QDialog):
    def __init__(self, config: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(420)
        self.config = dict(config)
        self.setStyleSheet(SETTINGS_STYLE)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        group = QGroupBox("Financial Parameters")
        form = QFormLayout()

        self.down_payment = QDoubleSpinBox()
        self.down_payment.setRange(0, 10_000_000)
        self.down_payment.setDecimals(0)
        self.down_payment.setPrefix("$")
        self.down_payment.setValue(self.config.get("down_payment_available", _FC.down_payment_available))
        form.addRow("Down payment available:", self.down_payment)

        self.income = QDoubleSpinBox()
        self.income.setRange(0, 10_000_000)
        self.income.setDecimals(0)
        self.income.setPrefix("$")
        self.income.setValue(self.config.get("annual_income", _FC.annual_income))
        form.addRow("Annual income:", self.income)

        self.debt = QDoubleSpinBox()
        self.debt.setRange(0, 100_000)
        self.debt.setDecimals(0)
        self.debt.setPrefix("$")
        self.debt.setValue(self.config.get("monthly_debt_obligations", _FC.monthly_debt_obligations))
        form.addRow("Monthly debt obligations:", self.debt)

        self.rate = QDoubleSpinBox()
        self.rate.setRange(0.001, 0.20)
        self.rate.setDecimals(4)
        self.rate.setSingleStep(0.0025)
        self.rate.setValue(self.config.get("contract_rate", _FC.contract_rate))
        form.addRow("Contract rate:", self.rate)

        self.amort = QSpinBox()
        self.amort.setRange(5, 30)
        self.amort.setValue(self.config.get("amortization_years", _FC.amortization_years))
        form.addRow("Amortization (years):", self.amort)

        self.gds = QDoubleSpinBox()
        self.gds.setRange(0.01, 1.0)
        self.gds.setDecimals(2)
        self.gds.setSingleStep(0.01)
        self.gds.setValue(self.config.get("gds_limit", _FC.gds_limit))
        form.addRow("GDS limit:", self.gds)

        self.tds = QDoubleSpinBox()
        self.tds.setRange(0.01, 1.0)
        self.tds.setDecimals(2)
        self.tds.setSingleStep(0.01)
        self.tds.setValue(self.config.get("tds_limit", _FC.tds_limit))
        form.addRow("TDS limit:", self.tds)

        self.heat = QDoubleSpinBox()
        self.heat.setRange(0, 1000)
        self.heat.setDecimals(0)
        self.heat.setPrefix("$")
        self.heat.setValue(self.config.get("lender_heat_estimate", _FC.lender_heat_estimate))
        form.addRow("Heat estimate (when not in fee):", self.heat)

        self.hydro = QDoubleSpinBox()
        self.hydro.setRange(0, 1000)
        self.hydro.setDecimals(0)
        self.hydro.setPrefix("$")
        self.hydro.setValue(self.config.get("hydro_estimate", _FC.hydro_estimate))
        form.addRow("Hydro estimate (when not in fee):", self.hydro)

        self.insurance = QDoubleSpinBox()
        self.insurance.setRange(0, 1000)
        self.insurance.setDecimals(0)
        self.insurance.setPrefix("$")
        self.insurance.setValue(self.config.get("insurance_estimate", _FC.insurance_estimate))
        form.addRow("Insurance estimate:", self.insurance)

        self.ftb = QCheckBox("First-time buyer (LTT rebate)")
        self.ftb.setChecked(self.config.get("first_time_buyer", _FC.first_time_buyer))
        form.addRow(self.ftb)

        group.setLayout(form)
        layout.addWidget(group)

        buttons = QHBoxLayout()
        save_btn = QPushButton("Save")
        save_btn.setObjectName("saveBtn")
        save_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        buttons.addStretch()
        buttons.addWidget(save_btn)
        buttons.addWidget(cancel_btn)
        layout.addLayout(buttons)

    def get_config(self) -> dict:
        return {
            **self.config,
            "down_payment_available": self.down_payment.value(),
            "annual_income": self.income.value(),
            "monthly_debt_obligations": self.debt.value(),
            "contract_rate": self.rate.value(),
            "amortization_years": self.amort.value(),
            "gds_limit": self.gds.value(),
            "tds_limit": self.tds.value(),
            "lender_heat_estimate": self.heat.value(),
            "hydro_estimate": self.hydro.value(),
            "insurance_estimate": self.insurance.value(),
            "first_time_buyer": self.ftb.isChecked(),
        }
