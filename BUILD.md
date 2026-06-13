# Building Device Manager as a desktop app

The GUI (`device_manager.py`) can be packaged into a standalone **macOS `.app`** and
**Windows `.exe`** with PyInstaller, using the shared `DeviceManager.spec`.

> PyInstaller does **not** cross-compile. Build the Mac app on a Mac, the Windows
> exe on Windows.

## Prerequisites (both platforms)
- Python 3.13 with the project deps installed in a venv:
  `pip install uiautomator2 "qrcode[pil]" opencv-python-headless flask pyngrok pyinstaller`
- App icon: `python make_icon.py` → writes `build_assets/icon.icns` and `icon.ico`.

## Bundled vs external tools
- **adb** and **ngrok** are **bundled inside the app** (from `bin/mac` / `bin/win`),
  so the target machine needs nothing for device control or tunnels.
- **scrcpy** is **not** bundled (it needs ffmpeg + SDL dylibs). The Mirror button
  requires scrcpy on PATH; everything else works without it.

---

## macOS  →  DeviceManager.app
```bash
./venv/bin/pyinstaller --noconfirm --clean DeviceManager.spec
open dist/DeviceManager.app
```
Output: `dist/DeviceManager.app` (~184 MB).

**Gatekeeper:** the app is unsigned, so the first launch shows "unidentified
developer". Either right-click → **Open**, or clear the quarantine flag:
```bash
xattr -dr com.apple.quarantine dist/DeviceManager.app
```
To distribute without warnings you'd need an Apple Developer ID signature +
notarization (`codesign --deep --sign "Developer ID Application: ..."` then
`xcrun notarytool submit`).

External tools are expected at `/opt/homebrew/bin` (Apple Silicon) or
`/usr/local/bin` (Intel); the app prepends these to PATH automatically. Install:
```bash
brew install android-platform-tools scrcpy ngrok
```

---

## Windows  →  DeviceManager.exe (single file)
1. Put the Windows binaries in **`bin\win\`** before building:
   - `adb.exe` + `AdbWinApi.dll` + `AdbWinUsbApi.dll` (from platform-tools)
   - `ngrok.exe`
2. Build (the spec auto-detects Windows and produces a **one-file** exe):
```bat
python make_icon.py
python -m PyInstaller --noconfirm --clean DeviceManager.spec
dist\DeviceManager.exe
```
Output: a single **`dist\DeviceManager.exe`** you can share directly.

scrcpy (optional, for Mirror) isn't bundled — install it and put it on PATH, or
in `C:\platform-tools` / `C:\ngrok` which the app also probes.
- scrcpy: https://github.com/Genymobile/scrcpy/releases

---

## Notes
- The HTTP API runs **in-process** (a daemon thread) inside the app, so no Python
  subprocess is spawned — required for the frozen build to work.
- `settings.json` (tokens, per-device credentials, watermarks) is written next to
  the executable's working directory.
- Rebuild after any code change: re-run the `pyinstaller` command above.
