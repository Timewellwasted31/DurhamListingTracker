"""QR code pairing dialog — desktop shows QR, mobile scans to connect."""

from __future__ import annotations

import json
import socket
from io import BytesIO
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap, QImage
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QHBoxLayout, QPushButton,
)

from listingtracker.paths import DATA_DIR


def _get_local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def _get_api_key() -> str:
    key_path = DATA_DIR / "api_key.txt"
    if key_path.exists():
        return key_path.read_text(encoding="utf-8").strip()
    return ""


class PairDialog(QDialog):
    def __init__(self, parent=None, port: int = 8000):
        super().__init__(parent)
        self.setWindowTitle("Pair Mobile Device")
        self.setFixedSize(420, 520)
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

        local_ip = _get_local_ip()
        api_key = _get_api_key()
        server_url = f"http://{local_ip}:{port}"

        self.pair_data = {"url": server_url, "key": api_key}
        self._generate_qr(json.dumps(self.pair_data))

        info_layout = QHBoxLayout()
        info_layout.setSpacing(6)
        ip_label = QLabel(f"Server: {server_url}")
        ip_label.setStyleSheet("font-size: 12px; color: #888;")
        info_layout.addWidget(ip_label)
        info_layout.addStretch()
        layout.addLayout(info_layout)

        hint = QLabel("Both devices must be on the same Wi-Fi network")
        hint.setAlignment(Qt.AlignCenter)
        hint.setStyleSheet("font-size: 11px; color: #666; font-style: italic;")
        layout.addWidget(hint)

        close_btn = QPushButton("Close")
        close_btn.setStyleSheet(
            "QPushButton { background: #333; color: #fff; border: none; "
            "border-radius: 6px; padding: 10px 24px; font-size: 14px; }"
            "QPushButton:hover { background: #444; }"
        )
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn, alignment=Qt.AlignCenter)

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
