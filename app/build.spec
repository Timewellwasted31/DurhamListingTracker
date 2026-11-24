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

# Patchright browsers are installed separately from the driver package.
# When frozen, patchright sets PLAYWRIGHT_BROWSERS_PATH=0 which makes it
# look for browsers in <driver>/package/.local-browsers/.
patchright_browsers_src = os.path.join(
    os.environ.get("LOCALAPPDATA", ""),
    "ms-playwright", "chromium_headless_shell-1248",
)
patchright_browsers_dest = os.path.join(
    "patchright", "driver", "package", ".local-browsers", "chromium_headless_shell-1248",
)

mpl_datas, mpl_binaries, mpl_hiddenimports = collect_all("matplotlib")

a = Analysis(
    ["listingtracker/__main__.py"],
    pathex=["W:\\ListingTracker\\app"],
    binaries=mpl_binaries,
    datas=[
        (patchright_driver, "patchright/driver"),
        (patchright_browsers_src, patchright_browsers_dest),
        # The phone page, and the phone app build the server offers for updating
        ("listingtracker/mobile", "listingtracker/mobile"),
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
        "qrcode",
    ] + mpl_hiddenimports
      # The phone server: uvicorn picks its event loop, HTTP and lifespan modules by name at runtime
      + collect_submodules("uvicorn"),
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

# Pieces the packaging tools pull in that the app never uses. Nothing kept depends on these Qt
# libraries (only the PDF and on-screen keyboard plugins do, and they go too). The hidden browser
# only needs its English language packs, and the app's windows have no translations.
UNUSED = (
    "PySide6/Qt6OpenGL.dll", "PySide6/opengl32sw.dll", "PySide6/Qt6Pdf.dll",
    "PySide6/Qt6Qml", "PySide6/Qt6Quick", "PySide6/Qt6VirtualKeyboard",
    "PySide6/plugins/imageformats/qpdf.dll",
    "PySide6/plugins/platforminputcontexts/qtvirtualkeyboardplugin.dll",
    "PySide6/translations/",
    "matplotlib/backends/_tkagg",
)
CHROMIUM_LOCALES = "chrome-headless-shell-win64/locales/"


def wanted(dest):
    dest = dest.replace("\\", "/")
    if dest.startswith(UNUSED):
        return False
    if CHROMIUM_LOCALES in dest:
        # en-GB is what it picks on this machine, so sites see the same language as before
        return dest.rsplit("/", 1)[-1].startswith(("en-US", "en-GB"))
    return True


a.binaries = [entry for entry in a.binaries if wanted(entry[0])]
a.datas = [entry for entry in a.datas if wanted(entry[0])]

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
