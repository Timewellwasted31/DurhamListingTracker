"""Site selection dialog for auto-search."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QCheckBox, QLabel,
    QPushButton, QFrame,
)
from PySide6.QtCore import Qt

from listingtracker.search import ALL_SEARCH_SITES

SITE_DESCRIPTIONS = {
    "strata.ca": "Condo-focused, excellent data quality",
    "condos.ca": "Condo-focused, good coverage",
    "realtor.ca": "Official MLS listings",
    "zolo.ca": "Aggregator with home value estimates",
    "housesigma.com": "Map-based search, sold data",
    "wahi.com": "Modern portal with estimates",
    "rew.ca": "National listing portal",
    "condonow.com": "Condo-specific with fee details",
    "condominiums.ca": "Condo building pages",
}


class SitePickerDialog(QDialog):
    def __init__(self, selected_sites: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Select Search Sites")
        self.setMinimumWidth(420)
        self.setStyleSheet("""
            QDialog { background: #1a1a2e; }
            QLabel { color: #e0e0e0; }
            QCheckBox { color: #e0e0e0; spacing: 8px; padding: 4px 0; }
            QCheckBox::indicator { width: 18px; height: 18px; }
            QPushButton {
                background: #3a3a5c; color: #e0e0e0; border: 1px solid #555;
                padding: 6px 16px; border-radius: 4px;
            }
            QPushButton:hover { background: #4a4a6c; }
            QPushButton#startBtn { background: #2e7d32; border-color: #4caf50; }
            QPushButton#startBtn:hover { background: #388e3c; }
        """)

        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        header = QLabel("Choose which websites to search:")
        header.setStyleSheet("font-size: 13px; font-weight: bold; padding-bottom: 4px;")
        layout.addWidget(header)

        # If no sites selected (first run or empty list), default to all
        if not selected_sites:
            selected_sites = list(ALL_SEARCH_SITES)

        self._checkboxes: dict[str, QCheckBox] = {}
        for site in ALL_SEARCH_SITES:
            desc = SITE_DESCRIPTIONS.get(site, "")
            cb = QCheckBox(f"{site}  —  {desc}" if desc else site)
            cb.setChecked(site in selected_sites)
            self._checkboxes[site] = cb
            layout.addWidget(cb)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color: #444;")
        layout.addWidget(sep)

        btn_row = QHBoxLayout()
        select_all = QPushButton("Select All")
        select_all.clicked.connect(lambda: self._set_all(True))
        deselect_all = QPushButton("Deselect All")
        deselect_all.clicked.connect(lambda: self._set_all(False))
        btn_row.addWidget(select_all)
        btn_row.addWidget(deselect_all)
        btn_row.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        start_btn = QPushButton("Start Search")
        start_btn.setObjectName("startBtn")
        start_btn.clicked.connect(self.accept)
        btn_row.addWidget(start_btn)

        layout.addLayout(btn_row)

    def _set_all(self, checked: bool):
        for cb in self._checkboxes.values():
            cb.setChecked(checked)

    def selected_sites(self) -> list[str]:
        return [site for site, cb in self._checkboxes.items() if cb.isChecked()]
