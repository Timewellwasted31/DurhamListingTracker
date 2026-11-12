# Durham Listing Tracker

Desktop application for tracking real estate listings in Durham Region, Ontario.
One-click "Recheck All" fetches every tracked listing and surfaces price/status changes.

## Setup

```bash
cd W:\ListingTracker\app
pip install -r requirements.txt
patchright install chrome
```

### Requirements

- Python 3.11+
- Google Chrome installed (the app uses your real Chrome via patchright)
- W: drive mounted (all data lives under `W:\ListingTracker\`)

## Run

```bash
cd W:\ListingTracker\app
python -m listingtracker
```

## Directory Layout

```
W:\ListingTracker\
├── app\            # Source code (read-only at runtime)
├── data\           # SQLite DB, config, CSV exports
├── cache\          # Gzipped HTML snapshots (disposable)
├── browser\        # Persistent Chrome profile (logins/cookies)
└── logs\           # Daily log files
```

- Deleting `cache\` loses only historical HTML — the app keeps working.
- Deleting `data\` resets to first-run state.
- `browser\profile\` holds real session cookies — do not share.

## Tests

```bash
cd W:\ListingTracker\app
python -m pytest tests/ -v
```

Covers: mortgage qualification math, change detection, adapter selectors,
and a path-safety test ensuring no file I/O targets outside `W:\ListingTracker\`.

## Supported Sites

1. strata.ca
2. condos.ca
3. realtor.ca
4. housesigma.com
5. zolo.ca

## Adding a Site

Write one adapter class in `app/listingtracker/adapters/` inheriting from
`SiteAdapter`, plus a fixture pair in `tests/fixtures/`. Nothing else needs
to change — the adapter is auto-registered by domain pattern.

## Anti-Detection

- Real Chrome via persistent profile with genuine cookies
- Random 8–25s delays between listings, longer pauses every 3rd
- Bezier mouse movement, random scroll/dwell
- Viewport jitter per session
- robots.txt respected
- On block: no retry, records BLOCKED status, surfaces "open in visible browser"
- Auto-disables after 3 consecutive blocks
