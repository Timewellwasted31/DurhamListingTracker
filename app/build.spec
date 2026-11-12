# -*- mode: python ; coding: utf-8 -*-
import os
import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

block_cipher = None

patchright_driver = os.path.join(
    os.environ.get("APPDATA", ""),
    "Python", "Python314", "site-packages", "patchright", "driver",
)

mpl_datas, mpl_binaries, mpl_hiddenimports = collect_all("matplotlib")

a = Analysis(
    ["listingtracker/__main__.py"],
    pathex=["W:\\ListingTracker\\app"],
    binaries=mpl_binaries,
    datas=[
        (patchright_driver, "patchright/driver"),
    ] + mpl_datas,
    hiddenimports=[
        "patchright",
        "patchright.sync_api",
        "patchright._impl",
        "patchright._impl._driver",
        "yaml",
        "plyer",
        "plyer.platforms.win",
        "plyer.platforms.win.notification",
        "curl_cffi",
    ] + mpl_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Don't exclude unittest: pyparsing (needed by matplotlib) imports it, and without it the
    # price chart fails to load in the packaged app.
    excludes=[
        "tkinter",
        "xmlrpc",
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="DurhamListingTracker",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="DurhamListingTracker",
)
