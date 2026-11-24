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
IMAGE_CACHE_DIR = CACHE_DIR / "images"
GEO_CACHE_PATH = CACHE_DIR / "geo_cache.db"
MPL_CACHE_DIR = CACHE_DIR / "matplotlib"  # chart library's font cache

# Site sign-ins: the sign-in window's browser profiles, and the kept sign-ins
BROWSER_DIR = ROOT / "browser"
BROWSER_PROFILE_DIR = BROWSER_DIR / "profile"

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
    IMAGE_CACHE_DIR,
    MPL_CACHE_DIR,
    BROWSER_DIR,
    BROWSER_PROFILE_DIR,
    LOGS_DIR,
]


def check_w_drive() -> bool:
    if getattr(sys, "frozen", False):
        return True
    return Path("W:\\").exists() and Path("W:\\").is_dir()


def ensure_directories() -> None:
    for d in RUNTIME_DIRS:
        d.mkdir(parents=True, exist_ok=True)
