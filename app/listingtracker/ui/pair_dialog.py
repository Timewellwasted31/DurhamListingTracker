"""QR code pairing dialog — desktop shows QR, mobile scans to connect."""

from __future__ import annotations

import json
import urllib.request
from io import BytesIO

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap, QImage
from PySide6.QtWidgets import (
    QApplication, QDialog, QGridLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget,
)

from listingtracker.remote import HTTP_PORT, api_key, lan_ip


DIALOG_WIDTH = 420


def _fetch_pair_info(port: int) -> dict:
    """Ask the API server on this computer how it can be reached; {} if it is not running."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/pair-info", timeout=2) as resp:
            return json.loads(resp.read())
    except (OSError, ValueError):
        return {}


class PairDialog(QDialog):
    def __init__(self, parent=None, port: int = HTTP_PORT):
        super().__init__(parent)
        self.setWindowTitle("Pair Mobile Device")
        self.setFixedWidth(DIALOG_WIDTH)
        self.setStyleSheet("""
            QDialog { background: #1a1a2e; }
            QLabel { color: #e0e0e0; }
        """)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(24, 24, 24, 24)

        title = QLabel("Pair Your Mobile Device")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("font-size: 18px; font-weight: bold; color: #fff;")
        layout.addWidget(title)

        desc = QLabel(
            "Open the Durham Listings app on your phone\n"
            "and tap 'Scan QR Code' to connect."
        )
        desc.setAlignment(Qt.AlignCenter)
        desc.setStyleSheet("font-size: 13px; color: #aaa; margin-bottom: 8px;")
        layout.addWidget(desc)

        self.qr_label = QLabel()
        self.qr_label.setAlignment(Qt.AlignCenter)
        self.qr_label.setFixedSize(300, 300)
        self.qr_label.setStyleSheet(
            "background: #fff; border-radius: 12px; padding: 10px;"
        )
        layout.addWidget(self.qr_label, alignment=Qt.AlignCenter)

        info = _fetch_pair_info(port)
        if info:
            hint_text = info.pop("remote", {}).get("message", "")
            self.pair_data = info
        else:
            hint_text = "The API server is not running, so only this Wi-Fi network will work"
            self.pair_data = {"url": f"http://{lan_ip()}:{port}", "key": api_key()}
        server_url = (self.pair_data.get("urls") or [self.pair_data["url"]])[0]
        self._generate_qr(json.dumps(self.pair_data, separators=(",", ":")))

        hint = QLabel(hint_text)
        hint.setAlignment(Qt.AlignCenter)
        hint.setWordWrap(True)
        hint.setStyleSheet("font-size: 11px; color: #666; font-style: italic;")
        layout.addWidget(hint)

        # Manual entry needs the certificate fingerprint on a secure address, so it is part of it
        fp = self.pair_data.get("fp", "")
        manual_address = f"{server_url}#{fp}" if server_url.startswith("https://") and fp else server_url

        self.details_btn = QPushButton("Show manual details")
        self.details_btn.setCursor(Qt.PointingHandCursor)
        self.details_btn.setStyleSheet(
            "QPushButton { background: none; color: #6c9eff; border: none; font-size: 13px; }"
            "QPushButton:hover { text-decoration: underline; }"
        )
        self.details_btn.clicked.connect(self._toggle_details)
        layout.addWidget(self.details_btn, alignment=Qt.AlignCenter)

        self.details = QWidget()
        grid = QGridLayout(self.details)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)
        for row, (label, value) in enumerate((("Server address", manual_address),
                                              ("API key", self.pair_data["key"]))):
            name = QLabel(label)
            name.setStyleSheet("font-size: 12px; color: #aaa;")
            field = QLineEdit(value)
            field.setReadOnly(True)
            field.setCursorPosition(0)
            field.setStyleSheet(
                "QLineEdit { background: #242444; color: #e8e8f0; border: 1px solid #2d2d5e; "
                "border-radius: 6px; padding: 6px 8px; font-family: Consolas, monospace; font-size: 12px; }"
            )
            copy_btn = QPushButton("Copy")
            copy_btn.setCursor(Qt.PointingHandCursor)
            copy_btn.setStyleSheet(
                "QPushButton { background: #333; color: #fff; border: none; border-radius: 6px; "
                "padding: 6px 12px; font-size: 12px; } QPushButton:hover { background: #444; }"
            )
            copy_btn.clicked.connect(lambda _=False, v=value, b=copy_btn: self._copy(v, b))
            grid.addWidget(name, row * 2, 0, 1, 2)
            grid.addWidget(field, row * 2 + 1, 0)
            grid.addWidget(copy_btn, row * 2 + 1, 1)
        note = QLabel("In the phone app, tap “Enter details manually” and enter both exactly as shown.")
        note.setWordWrap(True)
        note.setStyleSheet("font-size: 11px; color: #888;")
        grid.addWidget(note, 4, 0, 1, 2)
        self.details.setVisible(False)
        layout.addWidget(self.details)

        close_btn = QPushButton("Close")
        close_btn.setStyleSheet(
            "QPushButton { background: #333; color: #fff; border: none; "
            "border-radius: 6px; padding: 10px 24px; font-size: 14px; }"
            "QPushButton:hover { background: #444; }"
        )
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn, alignment=Qt.AlignCenter)
        self._fit_height()

    def _toggle_details(self):
        show = not self.details.isVisible()
        self.details.setVisible(show)
        self.details_btn.setText("Hide manual details" if show else "Show manual details")
        self._fit_height()

    def _fit_height(self):
        self.setFixedHeight(self.layout().totalHeightForWidth(DIALOG_WIDTH))

    def _copy(self, value: str, button: QPushButton):
        QApplication.clipboard().setText(value)
        button.setText("Copied")
        QTimer.singleShot(1500, lambda: button.setText("Copy"))

    def _generate_qr(self, data: str):
        try:
            import qrcode
            qr = qrcode.QRCode(
                version=None,
                error_correction=qrcode.constants.ERROR_CORRECT_M,
                box_size=8,
                border=2,
            )
            qr.add_data(data)
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white")
            buf = BytesIO()
            img.save(buf, format="PNG")
            buf.seek(0)
            qimage = QImage.fromData(buf.read())
            pixmap = QPixmap.fromImage(qimage)
            self.qr_label.setPixmap(
                pixmap.scaled(280, 280, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            )
        except ImportError:
            self.qr_label.setText(
                f"qrcode package not installed.\n\n"
                f"Manual pairing:\n"
                f"URL: {self.pair_data['url']}\n"
                f"Key: {self.pair_data['key'][:8]}..."
            )
            self.qr_label.setStyleSheet(
                "background: #2a2a3e; color: #aaa; border-radius: 12px; "
                "padding: 20px; font-size: 12px;"
            )
