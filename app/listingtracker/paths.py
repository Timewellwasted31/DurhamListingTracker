"""Single source of truth for every path in the application."""

from pathlib import Path
import sys


def _determine_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(r"W:\ListingTracker")


ROOT = _determine_root()

# Application code (read-only at runtime)
APP_DIR = ROOT / "app"
PACKAGE_DIR = APP_DIR / "listingtracker"

# User data (written at runtime)
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "listings.db"
CONFIG_PATH = DATA_DIR / "config.yaml"
EXPORTS_DIR = DATA_DIR / "exports"

# Cache (disposable)
CACHE_DIR = ROOT / "cache"
HTML_CACHE_DIR = CACHE_DIR / "html"
TILE_CACHE_DIR = CACHE_DIR / "tiles"
GEO_CACHE_PATH = CACHE_DIR / "geo_cache.db"
MPL_CACHE_DIR = CACHE_DIR / "matplotlib"  # chart library's font cache

# Browser profile (persistent logins)
BROWSER_DIR = ROOT / "browser"
BROWSER_PROFILE_DIR = BROWSER_DIR / "profile"
BROWSERS_DIR = BROWSER_DIR / "ms-playwright"  # the Chromium build the app drives

# Profiles
PROFILES_DIR = DATA_DIR / "profiles"

# Logs
LOGS_DIR = ROOT / "logs"

RUNTIME_DIRS = [
    DATA_DIR,
    EXPORTS_DIR,
    PROFILES_DIR,
    CACHE_DIR,
    HTML_CACHE_DIR,
    TILE_CACHE_DIR,
    MPL_CACHE_DIR,
    BROWSER_DIR,
    BROWSER_PROFILE_DIR,
    LOGS_DIR,
]


def use_installed_browsers(environ=None, app_browsers: Path = BROWSERS_DIR) -> None:
    """Point patchright at a Chromium install that exists.

    The app's own copy (BROWSERS_DIR) comes first, because it is there however the app was
    launched. Per-user installs live under AppData, which a program started from inside a
    sandboxed app may not see. Failing that, an inherited setting is kept if its folder exists,
    and the per-user ms-playwright folder is the last resort.
    """
    import os

    environ = os.environ if environ is None else environ
    if app_browsers.is_dir() and any(app_browsers.iterdir()):
        environ["PLAYWRIGHT_BROWSERS_PATH"] = str(app_browsers)
        return
    current = environ.get("PLAYWRIGHT_BROWSERS_PATH", "")
    if current and Path(current).is_dir():
        return
    local = environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    installed = Path(local) / "ms-playwright"
    if installed.is_dir():
        environ["PLAYWRIGHT_BROWSERS_PATH"] = str(installed)


def check_w_drive() -> bool:
    if getattr(sys, "frozen", False):
        return True
    return Path("W:\\").exists() and Path("W:\\").is_dir()


def ensure_directories() -> None:
    for d in RUNTIME_DIRS:
        d.mkdir(parents=True, exist_ok=True)
