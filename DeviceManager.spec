# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec — builds DeviceManager.app (macOS, onedir) or a single-file
# DeviceManager.exe (Windows). Build on the target OS:
#   ./venv/bin/pyinstaller DeviceManager.spec      (macOS)
#   python -m PyInstaller DeviceManager.spec        (Windows)
import sys
import os
from PyInstaller.utils.hooks import collect_all

WIN = sys.platform.startswith("win")
ASSETS = "build_assets"
ICON = os.path.join(ASSETS, "icon.ico" if WIN else "icon.icns")

datas, binaries, hiddenimports = [], [], []
for pkg in ("uiautomator2", "adbutils", "cv2", "pyngrok", "qrcode", "PIL",
            "flask", "werkzeug"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h
hiddenimports += ["api", "bcel", "qr_connect", "csl_client", "numpy"]

# bundle the platform's external binaries (adb, ngrok) into a 'bin' folder
_bin_src = "bin/win" if WIN else "bin/mac"
if os.path.isdir(_bin_src):
    for fn in os.listdir(_bin_src):
        datas.append((os.path.join(_bin_src, fn), "bin"))

# bundle the in-app user guide
if os.path.exists("USER_GUIDE.md"):
    datas.append(("USER_GUIDE.md", "."))

a = Analysis(
    ["device_manager.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["PyInstaller", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)

if WIN:
    # one-file executable
    exe = EXE(
        pyz, a.scripts, a.binaries, a.datas, [],
        name="DeviceManager",
        console=False,
        onefile=True,
        icon=ICON,
        disable_windowed_traceback=False,
    )
else:
    # macOS: onedir + .app bundle
    exe = EXE(
        pyz, a.scripts, [],
        exclude_binaries=True,
        name="DeviceManager",
        console=False,
        argv_emulation=True,
        icon=ICON,
    )
    coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False,
                   name="DeviceManager")
    app = BUNDLE(
        coll,
        name="DeviceManager.app",
        icon=ICON,
        bundle_identifier="com.csl.devicemanager",
        info_plist={"NSHighResolutionCapable": True, "LSUIElement": False},
    )
