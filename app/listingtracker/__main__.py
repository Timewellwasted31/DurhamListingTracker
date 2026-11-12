"""Entry point: python -m listingtracker"""

import os
import sys
import logging
from datetime import date
from pathlib import Path

from listingtracker.paths import (
    check_w_drive, ensure_directories, use_installed_browsers, LOGS_DIR, MPL_CACHE_DIR,
)

# Point patchright/playwright at the system-installed browsers, not the
# bundled driver's relative path (which breaks in PyInstaller builds).
use_installed_browsers()

# Keep the chart library's font cache with the app's other files instead of the user profile
os.environ["MPLCONFIGDIR"] = str(MPL_CACHE_DIR)

INSTANCE_NAME = "DurhamListingTracker-single-instance"


def setup_logging() -> None:
    log_file = LOGS_DIR / f"tracker-{date.today().isoformat()}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


def main() -> None:
    if not check_w_drive():
        try:
            from PySide6.QtWidgets import QApplication, QMessageBox

            app = QApplication(sys.argv)
            QMessageBox.critical(
                None,
                "Drive Not Found",
                "W: drive not found.\n\n"
                "This application stores all data on W:\\ListingTracker.\n"
                "Please ensure the drive is mounted and try again.",
            )
        except ImportError:
            print(
                "ERROR: W: drive not found. "
                "This application stores all data on W:\\ListingTracker.",
                file=sys.stderr,
            )
        sys.exit(1)

    from PySide6.QtWidgets import QApplication

    app = QApplication(sys.argv)
    app.setApplicationName("Durham Listing Tracker")

    # Two copies would share one database; hand over to the copy that is already open
    if show_running_instance():
        sys.exit(0)

    ensure_directories()
    setup_logging()

    logger = logging.getLogger("listingtracker")
    logger.info("Starting Durham Listing Tracker")

    from listingtracker.db import init_db

    init_db()

    from listingtracker.ui.main_window import MainWindow

    window = MainWindow()
    server = listen_for_other_instances(window)  # keep a reference for the app's lifetime
    window.show()
    exit_code = app.exec()
    server.close()
    sys.exit(exit_code)


def show_running_instance(name: str = INSTANCE_NAME) -> bool:
    """If the app is already open, ask that copy to come to the front. True when it was."""
    from PySide6.QtNetwork import QLocalSocket

    socket = QLocalSocket()
    socket.connectToServer(name)
    if not socket.waitForConnected(500):
        return False
    if sys.platform == "win32":
        # Windows only lets a window take focus when the foreground process allows it
        import ctypes
        ctypes.windll.user32.AllowSetForegroundWindow(-1)
    socket.write(b"show")
    socket.waitForBytesWritten(500)
    socket.disconnectFromServer()
    return True


def listen_for_other_instances(window, name: str = INSTANCE_NAME):
    """Bring `window` forward whenever another copy of the app is started."""
    from PySide6.QtCore import Qt
    from PySide6.QtNetwork import QLocalServer

    def bring_forward():
        while server.hasPendingConnections():
            server.nextPendingConnection().deleteLater()
        window.setWindowState(
            (window.windowState() & ~Qt.WindowState.WindowMinimized) | Qt.WindowState.WindowActive
        )
        window.show()
        window.raise_()
        window.activateWindow()

    QLocalServer.removeServer(name)  # clears a stale name left by a crash (no-op on Windows)
    server = QLocalServer(window)
    server.newConnection.connect(bring_forward)
    server.listen(name)
    return server


if __name__ == "__main__":
    if "--enrich-subprocess" in sys.argv:
        from listingtracker.fetcher import _enrich_subprocess_main
        _enrich_subprocess_main()
    else:
        main()
