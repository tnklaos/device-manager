# -*- mode: python ; coding: utf-8 -*-
# Builds the headless Python backend (server.py) as a standalone binary that the
# Electron app bundles and launches. Build on the target OS:
#   ./venv/bin/pyinstaller Backend.spec      (macOS)
#   python -m PyInstaller Backend.spec        (Windows)
import sys
import os
from PyInstaller.utils.hooks import collect_all

WIN = sys.platform.startswith("win")
datas, binaries, hiddenimports = [], [], []
for pkg in ("uiautomator2", "adbutils", "cv2", "pyngrok", "qrcode", "PIL",
            "flask", "werkzeug"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h
hiddenimports += ["engine", "bcel", "csl_client", "qr_connect", "numpy"]

_bin_src = "bin/win" if WIN else "bin/mac"
if os.path.isdir(_bin_src):
    for fn in os.listdir(_bin_src):
        datas.append((os.path.join(_bin_src, fn), "bin"))
if os.path.exists("USER_GUIDE.md"):
    datas.append(("USER_GUIDE.md", "."))

a = Analysis(
    ["server.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["PyInstaller", "pytest", "tkinter"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="backend",
    console=True,            # keep stdout; Electron hides the window via windowsHide
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="backend")
