"""Signing in to listing sites with the user's own accounts (see siteaccounts)."""

from __future__ import annotations

import logging
from typing import Optional

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtWidgets import (
    QGridLayout, QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout, QWidget,
)

from listingtracker import siteaccounts

logger = logging.getLogger(__name__)


class SignInWorker(QObject):
    """Keeps a site's sign-in window open until the user closes it."""
    finished = Signal(str, str)  # site key, error ("" when the window was simply closed)

    def __init__(self, key: str):
        super().__init__()
        self.key = key

    def run(self):
        try:
            siteaccounts.open_sign_in(self.key)
            self.finished.emit(self.key, "")
        except Exception as e:
            logger.error("Sign-in window for %s failed: %s", self.key, e, exc_info=True)
            self.finished.emit(self.key, str(e))


def start_sign_in(key: str, on_closed) -> tuple[QThread, SignInWorker]:
    """Open the site's sign-in window on a thread; `on_closed(key, error)` runs when it closes.
    The caller keeps the returned pair alive until then."""
    thread = QThread()
    worker = SignInWorker(key)
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    worker.finished.connect(on_closed)
    worker.finished.connect(thread.quit)
    thread.start()
    return thread, worker


def confirm_signed_in(parent, key: str, error: str) -> bool:
    """After the window closes: ask whether the user signed in, and remember the answer."""
    name = siteaccounts.SITES_BY_KEY[key].name
    if error:
        QMessageBox.warning(parent, "Sign-in window", f"The {name} window couldn't open:\n{error}")
        return False
    answer = QMessageBox.question(parent, f"Signed in to {name}?",
                                  f"Did you finish signing in to {name}?")
    if answer != QMessageBox.StandardButton.Yes:
        return False
    if not siteaccounts.load_session(key):
        QMessageBox.warning(parent, f"{name} sign-in",
                            f"The app couldn't keep the {name} sign-in. Try again, and wait for the "
                            f"site to show you as signed in before closing the window.")
        return False
    siteaccounts.mark_signed_in(key)
    return True


class SiteAccountsPanel(QWidget):
    """One row per site: whether the user is signed in, and buttons to sign in or out."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("plainPanel")
        self._thread: Optional[QThread] = None
        self._worker: Optional[SignInWorker] = None
        self._rows: dict[str, tuple[QLabel, QPushButton, QPushButton]] = {}

        layout = QVBoxLayout(self)
        browser, _ = siteaccounts.installed_browser()
        intro = QLabel(
            "Many sites show sold prices and listing history only to members. Sign in with your "
            "own free account and the app uses it when it rechecks, searches and reads building "
            "sales on that site.<br><br>"
            f"<b>Sign in</b> opens the site in {browser or 'your browser'}, in a separate window used only by this "
            "app. Sign in on the site as you normally would (email and password, or Google), "
            "wait until it shows you as signed in, then close the window. The app never sees "
            "your password: it keeps only the site's sign-in, encrypted so that only your "
            "Windows account can read it.<br><br>"
            "Sign-ins are shared by every profile on this computer. Signed-in sites are read one "
            "page at a time, at a person's pace, so the account isn't flagged.")
        intro.setWordWrap(True)
        intro.setStyleSheet("color: #aaa; font-size: 12px; padding: 4px 2px 10px 2px;")
        layout.addWidget(intro)

        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(8)
        for r, site in enumerate(siteaccounts.SITES):
            name = QLabel(f"<b>{site.name}</b><br><span style='color:#888'>{site.unlocks}</span>")
            name.setWordWrap(True)
            status = QLabel()
            sign_in = QPushButton()
            sign_in.clicked.connect(lambda _=False, k=site.key: self._sign_in(k))
            sign_out = QPushButton("Sign out")
            sign_out.clicked.connect(lambda _=False, k=site.key: self._sign_out(k))
            grid.addWidget(name, r, 0)
            grid.addWidget(status, r, 1)
            buttons = QHBoxLayout()
            buttons.addWidget(sign_in)
            buttons.addWidget(sign_out)
            grid.addLayout(buttons, r, 2)
            self._rows[site.key] = (status, sign_in, sign_out)
        grid.setColumnStretch(0, 1)
        layout.addLayout(grid)
        layout.addStretch()
        self._refresh()

    def busy(self) -> bool:
        return bool(self._thread and self._thread.isRunning())

    def _refresh(self, open_key: Optional[str] = None):
        from datetime import date
        for key, (status, sign_in, sign_out) in self._rows.items():
            since = siteaccounts.signed_in_on(key)
            if key == open_key:
                status.setText("<span style='color:#ffb74d'>Window open: sign in, then close it</span>")
            elif since:
                status.setText(f"<span style='color:#81c784'>Signed in</span> "
                               f"<span style='color:#888'>since {date.fromisoformat(since):%b %d}</span>")
            else:
                status.setText("<span style='color:#888'>Not signed in</span>")
            sign_in.setText("Sign in again" if since else "Sign in")
            sign_in.setEnabled(open_key is None)
            sign_out.setEnabled(open_key is None and bool(since or siteaccounts.load_session(key)))

    def _sign_in(self, key: str):
        if self.busy():
            return
        self._thread, self._worker = start_sign_in(key, self._on_closed)
        self._refresh(open_key=key)

    def _on_closed(self, key: str, error: str):
        confirm_signed_in(self, key, error)
        self._refresh()

    def _sign_out(self, key: str):
        name = siteaccounts.SITES_BY_KEY[key].name
        answer = QMessageBox.question(
            self, f"Sign out of {name}",
            f"Forget the {name} sign-in on this computer? The app will read {name} as a visitor "
            f"until you sign in again.")
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            siteaccounts.forget(key)
        except siteaccounts.Busy as e:
            QMessageBox.information(self, f"Sign out of {name}", f"{e}. Try again when it's finished.")
        self._refresh()
