"""
Device Manager - compact control panel for many Android devices.

Sidebar + a list of connected devices, each with Play/Stop/Mirror/Disconnect.
Play runs the configured execute file (default: automate.py) for that device,
passing the device serial as an argument.

Run:  ./venv/bin/python device_manager.py
"""
import os
import sys
import json
import time
import subprocess
import threading
import tkinter as tk
from tkinter import filedialog
import qr_connect

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable  # use the same (venv) python that launched this GUI

# Binaries bundled inside the frozen app (adb, ngrok) take priority.
import stat
if getattr(sys, "frozen", False):
    _bundle_bin = os.path.join(getattr(sys, "_MEIPASS", HERE), "bin")
    if os.path.isdir(_bundle_bin):
        os.environ["PATH"] = _bundle_bin + os.pathsep + os.environ.get("PATH", "")
        for _b in ("adb", "ngrok", "adb.exe", "ngrok.exe"):
            _bp = os.path.join(_bundle_bin, _b)
            if os.path.exists(_bp):
                try:
                    os.chmod(_bp, os.stat(_bp).st_mode | stat.S_IEXEC |
                             stat.S_IXGRP | stat.S_IXOTH)
                except OSError:
                    pass

# When launched from Finder/Explorer the PATH is minimal, so external CLI tools
# (adb / scrcpy / ngrok) aren't found. Prepend their common install locations
# (used as a fallback, e.g. for scrcpy which isn't bundled).
for _p in ("/opt/homebrew/bin", "/usr/local/bin",
           os.path.expanduser("~/Library/Android/sdk/platform-tools"),
           os.path.expanduser("~/AppData/Local/Android/Sdk/platform-tools"),
           r"C:\platform-tools", r"C:\ngrok"):
    if os.path.isdir(_p) and _p not in os.environ.get("PATH", ""):
        os.environ["PATH"] = os.environ.get("PATH", "") + os.pathsep + _p
SETTINGS_FILE = os.path.join(HERE, "settings.json")
API_PORT = 8000
CSL_API_URL = "https://paymentgateway.108pay.co"
# On Windows, hide the console window that pops up for each child process (adb,
# scrcpy, …). 0 on macOS/Linux, so behaviour there is unchanged.
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
MONITOR_INTERVAL = 60   # seconds between message polls
NOTIF_INTERVAL = 20     # seconds between notification polls (they get dismissed)
MONITOR_KINDS = {"TRI"}  # only report these message kinds (TRI = received transfer)
WEBHOOK_URL = "http://localhost:2192/new-transaction"   # default setup webhook URL


def load_settings():
    try:
        with open(SETTINGS_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def save_settings(data):
    with open(SETTINGS_FILE, "w") as f:
        json.dump(data, f, indent=2)


def detect_running_tunnel():
    """Return the public https URL of an ngrok tunnel already running on this
    machine (via ngrok's local API on :4040), or None."""
    try:
        import requests
        r = requests.get("http://127.0.0.1:4040/api/tunnels", timeout=2)
        for t in r.json().get("tunnels", []):
            url = t.get("public_url", "")
            if url.startswith("https"):
                return url
    except Exception:
        pass
    return None

# ---- modern dark theme ----
BG = "#0d1117"
SIDEBAR = "#0a0e15"
CARD = "#161b26"
CARD_H = "#1c2230"
LINE = "#222a38"
INDIGO = "#6366f1"
INDIGO_H = "#4f52e5"
GREEN = "#22c55e"
GREEN_H = "#1aa34a"
RED = "#ef4444"
RED_H = "#dc2626"
PURPLE = "#a855f7"
PURPLE_H = "#9333ea"
SLATE = "#2b3445"
SLATE_H = "#374256"
TEXT = "#e6edf3"
SUBTEXT = "#7d8590"
MUTED_BG = "#1a212e"
MUTED_FG = "#4a5365"

FONT = "SF Pro Display"  # falls back to default if unavailable


from PIL import Image, ImageDraw, ImageTk

_btn_img_cache = {}


def _rounded_image(w, h, r, color):
    """Anti-aliased rounded-rect RGBA image (supersample 4x then downscale)."""
    key = (w, h, r, color)
    if key in _btn_img_cache:
        return _btn_img_cache[key]
    s = 4
    img = Image.new("RGBA", (w * s, h * s), (0, 0, 0, 0))
    ImageDraw.Draw(img).rounded_rectangle([0, 0, w * s - 1, h * s - 1],
                                          radius=r * s, fill=color)
    photo = ImageTk.PhotoImage(img.resize((w, h), Image.LANCZOS))
    _btn_img_cache[key] = photo
    return photo


class Btn(tk.Canvas):
    """A rounded-rectangle button with smooth (anti-aliased) corners. Same API:
    Btn(master, text, color, hover, command, ...)."""
    def __init__(self, master, text, color, hover, command,
                 fg="white", padx=14, pady=6, font_size=11, bold=True, radius=9):
        self._font = (FONT, font_size, "bold" if bold else "normal")
        probe = tk.Label(master, text=text, font=self._font)
        tw, th = probe.winfo_reqwidth(), probe.winfo_reqheight()
        probe.destroy()
        bw, bh = tw + padx * 2, th + pady * 2
        try:
            parent_bg = master["bg"]
        except tk.TclError:
            parent_bg = BG
        super().__init__(master, width=bw, height=bh, bg=parent_bg,
                         highlightthickness=0, cursor="hand2")
        self.command, self.fg = command, fg
        self.enabled = True
        # pre-render the three states (cached across identical buttons)
        self._img_base = _rounded_image(bw, bh, radius, color)
        self._img_hover = _rounded_image(bw, bh, radius, hover)
        self._img_dis = _rounded_image(bw, bh, radius, MUTED_BG)
        self._bgid = self.create_image(0, 0, anchor="nw", image=self._img_base)
        self._txtid = self.create_text(bw // 2, bh // 2, text=text, fill=fg,
                                       font=self._font)
        self.bind("<Button-1>", self._click)
        self.bind("<Enter>", lambda e: self.enabled and
                  self.itemconfig(self._bgid, image=self._img_hover))
        self.bind("<Leave>", lambda e: self.enabled and
                  self.itemconfig(self._bgid, image=self._img_base))

    def _click(self, _):
        if self.enabled and self.command:
            self.command()

    def set_enabled(self, on):
        self.enabled = on
        self.config(cursor="hand2" if on else "")
        self.itemconfig(self._bgid, image=self._img_base if on else self._img_dis)
        self.itemconfig(self._txtid, fill=self.fg if on else MUTED_FG)


def adb(*args):
    return subprocess.run(["adb", *args], capture_output=True, text=True,
                          creationflags=NO_WINDOW).stdout


def device_online(serial):
    """True if `serial` is currently connected and in the 'device' state."""
    out = adb("devices")
    for line in out.splitlines()[1:]:
        line = line.strip()
        if not line or "_adb-tls-" in line:
            continue
        parts = line.split()
        if parts[0] == serial and parts[-1] == "device":
            return True
    return False


def list_devices():
    out = adb("devices", "-l")
    devices = []
    for line in out.splitlines()[1:]:
        line = line.strip()
        if not line or "_adb-tls-" in line:
            continue
        parts = line.split()
        serial, state = parts[0], parts[1]
        model = ""
        for p in parts[2:]:
            if p.startswith("model:"):
                model = p.split(":", 1)[1].replace("_", " ")
        devices.append((serial, model or "Android device", state))
    return devices


class DeviceRow(tk.Frame):
    def __init__(self, master, app, serial, model, state):
        super().__init__(master, bg=BG)
        self.app = app
        self.serial = serial
        self.proc = None
        self.stopped = False
        self.monitor_active = False
        self.listen_active = False
        self._seen_notif = set()
        self.alive = True

        self.card = tk.Frame(self, bg=CARD, height=50)
        self.card.pack(fill="x", padx=16, pady=3)
        self.card.pack_propagate(False)
        inner = tk.Frame(self.card, bg=CARD)
        inner.pack(fill="both", expand=True, padx=14, pady=6)

        # hover highlight on the whole row
        for w in (self.card, inner):
            w.bind("<Enter>", lambda e: self._tint(CARD_H))
            w.bind("<Leave>", lambda e: self._tint(CARD))

        # status dot
        self.dot = tk.Canvas(inner, width=10, height=10, bg=CARD, highlightthickness=0)
        self.dot.pack(side="left", padx=(2, 12))
        self._draw_dot(GREEN if state == "device" else SUBTEXT)

        # name + serial stacked, compact
        meta = tk.Frame(inner, bg=CARD)
        meta.pack(side="left", fill="y")
        self.l_model = tk.Label(meta, text=model, bg=CARD, fg=TEXT,
                                font=(FONT, 13, "bold"))
        self.l_model.pack(anchor="w")
        self.l_serial = tk.Label(meta, text=serial, bg=CARD, fg=SUBTEXT,
                                 font=("Menlo", 10))
        self.l_serial.pack(anchor="w")

        # action buttons (right) — compact text labels
        btns = tk.Frame(inner, bg=CARD)
        btns.pack(side="right")
        self.play_btn = Btn(btns, "Play", GREEN, GREEN_H, self.play, padx=9, font_size=10)
        self.play_btn.pack(side="left", padx=2)
        self.listen_btn = Btn(btns, "Listen", "#0ea5e9", "#0284c7", self.listen,
                              padx=9, font_size=10)
        self.listen_btn.pack(side="left", padx=2)
        self.stop_btn = Btn(btns, "Stop", RED, RED_H, self.stop, padx=9, font_size=10)
        self.stop_btn.pack(side="left", padx=2)
        self.stop_btn.set_enabled(False)
        self.set_btn = Btn(btns, "Set", SLATE, SLATE_H, self.open_settings, padx=9, font_size=10)
        self.set_btn.pack(side="left", padx=2)
        self.mirror_btn = Btn(btns, "Mirror", SLATE, SLATE_H, self.mirror, padx=9, font_size=10)
        self.mirror_btn.pack(side="left", padx=2)
        self.disc_btn = Btn(btns, "Disconnect", SLATE, SLATE_H, self.disconnect, padx=9, font_size=10)
        self.disc_btn.pack(side="left", padx=2)

        # status pill (right, before buttons)
        self.status = tk.Label(inner, text="idle", bg=MUTED_BG, fg=SUBTEXT,
                               font=(FONT, 10, "bold"), padx=8, pady=2)
        self.status.pack(side="right", padx=10)

        # tint the gear green if this device already has stored credentials
        if self.app.device_creds(serial).get("password"):
            self._mark_creds(True)

    def _tint(self, color):
        for w in [self.card] + list(self.card.winfo_children()):
            try:
                w.config(bg=color)
            except tk.TclError:
                pass
        for child in self.card.winfo_children():
            for sub in child.winfo_children():
                if not isinstance(sub, Btn):
                    try:
                        sub.config(bg=color)
                    except tk.TclError:
                        pass

    def _draw_dot(self, color):
        self.dot.delete("all")
        self.dot.create_oval(1, 1, 9, 9, fill=color, outline="")

    def set_status(self, text, fg, bg=MUTED_BG):
        self.status.config(text=text, fg=fg, bg=bg)

    # ---- actions: message monitor (polls every MONITOR_INTERVAL seconds) ----
    def play(self):
        if self.monitor_active:
            return
        self.monitor_active = True
        self.play_btn.set_enabled(False)
        self.stop_btn.set_enabled(True)
        self._draw_dot(INDIGO)
        self.set_status("monitoring", "#c7d2fe", "#312e81")
        self.app.log(f"▶ {self.serial}: message monitor started (every "
                     f"{MONITOR_INTERVAL}s)")
        self.app.update_counter()
        threading.Thread(target=self._monitor_loop, daemon=True).start()

    def _post(self, fn):
        # skip if the row was destroyed (e.g. by a Refresh) — avoids errors
        self.app.root.after(0, lambda: self.alive and fn())

    def _monitor_loop(self):
        import bcel
        import api
        while self.monitor_active:
            # if the device dropped off ADB, stop monitoring it
            if not device_online(self.serial):
                self._post(self._monitor_disconnected)
                return
            lock = api.device_lock(self.serial)
            if not lock.acquire(blocking=False):
                self._post(lambda: self.app.log(
                    f"· {self.serial}: device busy — skipping this message poll"))
                for _ in range(MONITOR_INTERVAL):
                    if not self.monitor_active:
                        break
                    time.sleep(1)
                continue
            try:
                creds = self.app.device_creds(self.serial)
                pwd, user = creds.get("password", ""), creds.get("username", "")
                last_ref = self.app.device_creds(self.serial).get("last_ref") or None
                self._post(lambda: self.app.log(f"⟳ {self.serial}: refreshing messages…"))
                res = bcel.poll_messages(self.serial, last_ref, pwd, user,
                                         kinds=MONITOR_KINDS,
                                         log=lambda m: self._post(
                                             lambda m=m: self.app.log(f"   {self.serial}: {m}")))
                new = res.get("new") or []
                # send first (so last_ref is only advanced after a successful send)
                if new:
                    for m in new:
                        self._post(lambda m=m: self.app.log(
                            f"📩 {self.serial} NEW {m.get('type')} | {m.get('ref')} | "
                            f"{' '.join(m.get('raw', []))[:80]}"))
                    self._post(lambda n=len(new):
                               self.app.notify(f"{self.serial}: {n} new transaction(s)", color=GREEN))
                    self._send_webhook(new)            # POST array to the API
                elif res.get("first_run"):
                    self._post(lambda r=res.get("last_ref"): self.app.log(
                        f"✓ {self.serial}: baseline set ({r}) — nothing sent"))
                else:
                    self._post(lambda: self.app.log(f"· {self.serial}: no new transactions"))
                # advance the watermark to the newest seen (first run sends all,
                # then sets last_ref to the top; later runs stop at last_ref)
                if res.get("last_ref"):
                    self._post(lambda r=res["last_ref"]:
                               self.app.set_device_value(self.serial, "last_ref", r))
            except Exception as e:
                # a disconnect often surfaces as a poll error — verify and stop
                if not device_online(self.serial):
                    self._post(self._monitor_disconnected)
                    return
                self._post(lambda e=e: self.app.log(f"✗ {self.serial}: {e}"))
            finally:
                lock.release()
            # wait the interval, but react to Stop within ~1s
            for _ in range(MONITOR_INTERVAL):
                if not self.monitor_active:
                    break
                time.sleep(1)
        self._post(self._monitor_stopped)

    def _monitor_disconnected(self):
        self.monitor_active = False
        self.play_btn.set_enabled(True)
        self.stop_btn.set_enabled(False)
        self._draw_dot(RED)
        self.set_status("disconnected", "#fecaca", "#7f1d1d")
        self.app.log(f"⚠ {self.serial}: device disconnected — monitor stopped")
        self.app.notify(f"{self.serial} disconnected — monitor stopped", color=RED)
        self.app.update_counter()

    # ---- notification listener (pure adb, no agent / login / UI driving) ----
    def listen(self):
        if self.listen_active or self.monitor_active:
            return
        self.listen_active = True
        self._seen_notif = set()
        self.play_btn.set_enabled(False)
        self.listen_btn.set_enabled(False)
        self.stop_btn.set_enabled(True)
        self._draw_dot("#0ea5e9")
        self.set_status("listening", "#bae6fd", "#075985")
        self.app.log(f"👂 {self.serial}: notification listener started (every "
                     f"{NOTIF_INTERVAL}s)")
        self.app.update_counter()
        threading.Thread(target=self._listen_loop, daemon=True).start()

    def _listen_loop(self):
        import bcel
        first = True
        while self.listen_active:
            if not device_online(self.serial):
                self._post(self._listen_disconnected)
                return
            try:
                notes = bcel.read_notifications(self.serial)
                keyof = lambda n: n.get("key") or (n.get("title", "") + n.get("when", ""))
                new = [n for n in notes if keyof(n) not in self._seen_notif]
                for n in notes:
                    self._seen_notif.add(keyof(n))
                if first:
                    first = False
                    self._post(lambda c=len(notes): self.app.log(
                        f"👂 {self.serial}: baseline ({c} BCEL notif on screen)"))
                elif new:
                    txns = [bcel.notification_to_txn(n, self.serial) for n in new]
                    for t in txns:
                        self._post(lambda t=t: self.app.log(
                            f"🔔 {self.serial} NOTIF {t['type']} | {' '.join(t['raw'])[:70]}"))
                    tri = [t for t in txns if t["kind"] in MONITOR_KINDS]
                    if tri:
                        self._send_webhook(tri)
            except Exception as e:
                if not device_online(self.serial):
                    self._post(self._listen_disconnected)
                    return
                self._post(lambda e=e: self.app.log(f"✗ {self.serial}: {e}"))
            for _ in range(NOTIF_INTERVAL):
                if not self.listen_active:
                    break
                time.sleep(1)
        self._post(self._listen_stopped)

    def _listen_stopped(self):
        self.play_btn.set_enabled(True)
        self.listen_btn.set_enabled(True)
        self.stop_btn.set_enabled(False)
        self._draw_dot(GREEN)
        self.set_status("idle", SUBTEXT)
        self.app.log(f"■ {self.serial}: listener stopped")
        self.app.update_counter()

    def _listen_disconnected(self):
        self.listen_active = False
        self.play_btn.set_enabled(True)
        self.listen_btn.set_enabled(True)
        self.stop_btn.set_enabled(False)
        self._draw_dot(RED)
        self.set_status("disconnected", "#fecaca", "#7f1d1d")
        self.app.log(f"⚠ {self.serial}: device disconnected — listener stopped")
        self.app.notify(f"{self.serial} disconnected — listener stopped", color=RED)
        self.app.update_counter()

    def _send_webhook(self, new_msgs):
        """POST new transactions to the saved CSL API /bcel/transactions path.
        No new transactions (e.g. the top message's ref == the saved last_ref)
        means nothing to sync — skip the API call entirely."""
        if not new_msgs:
            return
        transactions = [{**m, "serial": self.serial} for m in new_msgs]
        cfg = self.app.settings.get("csl", {})
        api_url = CSL_API_URL
        client_id = (cfg.get("client_id") or "").strip()
        api_key = (cfg.get("api_key") or "").strip()
        if not all((api_url, client_id, api_key)):
            self._post(lambda: self.app.log(
                f"⚠ {self.serial}: sync credentials missing — transactions not sent"))
            return
        try:
            import csl_client
            ok, msg = csl_client.post_transactions(
                api_url, client_id, api_key, transactions, timeout=10)
            self._post(lambda: self.app.log(
                f"→ {self.serial}: synced {len(transactions)} transaction(s): {msg}"))
            if not ok:
                self._post(lambda: self.app.notify(
                    f"{self.serial}: transaction send failed", color=RED))
        except Exception as e:
            self._post(lambda e=e: self.app.log(
                f"⚠ {self.serial}: transaction sync failed: {e}"))

    def _monitor_stopped(self):
        self.play_btn.set_enabled(True)
        self.stop_btn.set_enabled(False)
        self._draw_dot(GREEN)
        self.set_status("idle", SUBTEXT)
        self.app.log(f"■ {self.serial}: monitor stopped")
        self.app.update_counter()

    def stop(self):
        if self.monitor_active or self.listen_active:
            self.monitor_active = False     # loops exit after current cycle
            self.listen_active = False
            self.set_status("stopping…", SUBTEXT)
        self.stop_btn.set_enabled(False)

    def mirror(self):
        subprocess.Popen(["scrcpy", "--serial", self.serial,
                          "--window-title", self.serial],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=NO_WINDOW)
        self.app.notify(f"Mirroring {self.serial}", color=SLATE_H)

    def disconnect(self):
        if self.monitor_active:
            self.stop()
        if ":" in self.serial:
            adb("disconnect", self.serial)
            self.app.notify(f"Disconnected {self.serial}", color=PURPLE_H)
        else:
            self.app.notify(f"{self.serial} is USB — unplug to disconnect", color=RED)
        self.app.refresh()

    def destroy(self):
        self.alive = False
        self.monitor_active = False     # signal the monitor/listener threads to stop
        self.listen_active = False
        super().destroy()

    def open_settings(self):
        """Per-device credentials dialog (username + password)."""
        creds = self.app.device_creds(self.serial)
        win = tk.Toplevel(self.app.root, bg=CARD)
        win.title("Device settings")
        win.geometry("400x350")
        win.configure(bg=CARD)
        win.transient(self.app.root)

        tk.Label(win, text="Device settings", bg=CARD, fg=TEXT,
                 font=(FONT, 14, "bold")).pack(anchor="w", padx=18, pady=(16, 0))
        tk.Label(win, text=f"{self.l_model.cget('text')}  ·  {self.serial}", bg=CARD,
                 fg=SUBTEXT, font=("Menlo", 10)).pack(anchor="w", padx=18, pady=(0, 12))

        user_var = tk.StringVar(value=creds.get("username", ""))
        pwd_var = tk.StringVar(value=creds.get("password", ""))
        ref_var = tk.StringVar(value=creds.get("last_ref", "") or "")

        tk.Label(win, text="Username", bg=CARD, fg=SUBTEXT,
                 font=(FONT, 10)).pack(anchor="w", padx=18)
        tk.Entry(win, textvariable=user_var, bg=BG, fg=TEXT, insertbackground=TEXT,
                 relief="flat", font=("Menlo", 11)).pack(fill="x", padx=18, ipady=5, pady=(2, 10))
        tk.Label(win, text="Password", bg=CARD, fg=SUBTEXT,
                 font=(FONT, 10)).pack(anchor="w", padx=18)
        pwd_entry = tk.Entry(win, textvariable=pwd_var, bg=BG, fg=TEXT, show="•",
                             insertbackground=TEXT, relief="flat", font=("Menlo", 11))
        pwd_entry.pack(fill="x", padx=18, ipady=5, pady=(2, 10))

        # last reference (message-monitor watermark)
        reflabel = tk.Frame(win, bg=CARD)
        reflabel.pack(fill="x", padx=18)
        tk.Label(reflabel, text="Last reference (monitor watermark)", bg=CARD,
                 fg=SUBTEXT, font=(FONT, 10)).pack(side="left")
        Btn(reflabel, "Reset", SLATE, SLATE_H, lambda: ref_var.set(""),
            padx=8, font_size=9).pack(side="right")
        refrow = tk.Frame(win, bg=CARD)
        refrow.pack(fill="x", padx=18, pady=(2, 4))
        tk.Entry(refrow, textvariable=ref_var, bg=BG, fg=TEXT, insertbackground=TEXT,
                 relief="flat", font=("Menlo", 11)).pack(fill="x", ipady=5)
        tk.Label(win, text="Leave blank to re-set the baseline on the next poll.",
                 bg=CARD, fg=MUTED_FG, font=(FONT, 9)).pack(anchor="w", padx=18, pady=(0, 12))

        def save():
            self.app.save_device_creds(self.serial, user_var.get().strip(), pwd_var.get())
            self.app.set_device_value(self.serial, "last_ref", ref_var.get().strip())
            self._mark_creds(True)
            self.app.notify(f"Saved settings for {self.serial}", color=GREEN)
            win.destroy()

        bar = tk.Frame(win, bg=CARD)
        bar.pack(fill="x", padx=18)
        Btn(bar, "Save", GREEN, GREEN_H, save, padx=16).pack(side="left")
        Btn(bar, "Cancel", SLATE, SLATE_H, win.destroy, padx=16).pack(side="left", padx=8)
        pwd_entry.focus_set()

    def _mark_creds(self, has):
        # subtle hint on the gear when creds are stored
        self.set_btn.itemconfig(self.set_btn._txtid, fill=GREEN if has else "white")


class App:
    def __init__(self, root):
        self.root = root
        root.title("Device Manager")
        root.geometry("1040x600")
        root.minsize(940, 480)
        root.configure(bg=BG)
        self.rows = []
        self.script_path = tk.StringVar(value=os.path.join(HERE, "automate.py"))

        # ---- persisted settings + tunnel/API state ----
        self.settings = load_settings()
        self.ngrok_token = tk.StringVar(value=self.settings.get("ngrok_token", ""))
        self.api_proc = None
        self.public_url = None

        # ---- sidebar ----
        self.nav_items = {}
        sidebar = tk.Frame(root, bg=SIDEBAR, width=158)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)
        tk.Label(sidebar, text="◆  Manager", bg=SIDEBAR, fg=TEXT,
                 font=(FONT, 15, "bold")).pack(anchor="w", pady=(22, 18), padx=18)
        for icon, name in [("▣", "Devices"), ("⚙", "Settings"), ("≡", "Logs")]:
            lbl = tk.Label(sidebar, text=f"  {icon}  {name}", bg=SIDEBAR, fg=SUBTEXT,
                           anchor="w", font=(FONT, 12), cursor="hand2")
            lbl.pack(fill="x", padx=10, pady=2, ipady=8)
            lbl.bind("<Button-1>", lambda e, n=name: self.show_view(n))
            self.nav_items[name] = lbl
        # footer: tunnel status + device count
        self.side_tunnel = tk.Label(sidebar, text="● Sync: off", bg=SIDEBAR, fg=SUBTEXT,
                                    font=(FONT, 9), anchor="w")
        self.side_tunnel.pack(side="bottom", anchor="w", padx=18, pady=(0, 14))
        self.side_count = tk.Label(sidebar, text="", bg=SIDEBAR, fg=SUBTEXT,
                                   font=(FONT, 10), anchor="w")
        self.side_count.pack(side="bottom", anchor="w", padx=18, pady=2)

        # ---- main area: a swappable view container + shared console ----
        main = tk.Frame(root, bg=BG)
        main.pack(side="left", fill="both", expand=True)
        self.view_container = tk.Frame(main, bg=BG)
        self.view_container.pack(fill="both", expand=True)
        self.console = tk.Text(main, height=5, bg="#080b11", fg="#7ee787",
                               insertbackground=TEXT, relief="flat", font=("Menlo", 10),
                               padx=12, pady=6, highlightthickness=1,
                               highlightbackground=LINE)
        self.console.pack(fill="x", padx=16, pady=(6, 10))

        self.devices_view = self.build_devices_view(self.view_container)
        self.settings_view = self.build_settings_view(self.view_container)
        self.show_view("Devices")
        self.refresh()
        self.refresh_tunnel_status()   # reflect an already-running tunnel

    # ---- views ----
    def build_devices_view(self, parent):
        v = tk.Frame(parent, bg=BG)

        # header: title + counter (left), bulk actions (right)
        head = tk.Frame(v, bg=BG)
        head.pack(fill="x", padx=16, pady=(14, 4))
        tk.Label(head, text="Devices", bg=BG, fg=TEXT,
                 font=(FONT, 17, "bold")).pack(side="left")
        self.counter = tk.Label(head, text="0 / 0", bg=MUTED_BG, fg=SUBTEXT,
                                font=(FONT, 10, "bold"), padx=9, pady=2)
        self.counter.pack(side="left", padx=9, pady=(5, 0))
        Btn(head, "↻", INDIGO, INDIGO_H, self.refresh, padx=10, font_size=12).pack(side="right")
        Btn(head, "Disconnect All", PURPLE, PURPLE_H, self.disconnect_all, padx=10, font_size=10).pack(side="right", padx=6)
        Btn(head, "Run All", GREEN, GREEN_H, self.run_all, padx=10, font_size=10).pack(side="right")

        # slim connect bar
        strip = tk.Frame(v, bg=CARD)
        strip.pack(fill="x", padx=16, pady=(2, 6))
        inner = tk.Frame(strip, bg=CARD)
        inner.pack(fill="x", padx=8, pady=6)
        self.connect_ip = tk.StringVar(value="")
        ent = tk.Entry(inner, textvariable=self.connect_ip, bg=BG, fg=TEXT,
                       insertbackground=TEXT, relief="flat", width=20, font=("Menlo", 11))
        ent.pack(side="left", ipady=5)
        ent.bind("<Return>", lambda e: self.connect_device())
        Btn(inner, "Connect", INDIGO, INDIGO_H, self.connect_device, padx=11).pack(side="left", padx=6)
        Btn(inner, "📷 QR", GREEN, GREEN_H, self.show_qr_dialog, padx=11).pack(side="left")

        self.list_frame = tk.Frame(v, bg=BG)
        self.list_frame.pack(fill="both", expand=True, pady=(2, 0))
        return v

    def build_settings_view(self, parent):
        v = tk.Frame(parent, bg=BG)
        tk.Label(v, text="Settings", bg=BG, fg=TEXT,
                 font=(FONT, 18, "bold")).pack(anchor="w", padx=18, pady=(16, 10))

        # ---- device setting card ----
        csl = self.settings.get("csl", {})
        self.url_var = tk.StringVar(value="")
        self.csl_api_url = tk.StringVar(value=CSL_API_URL)
        self.csl_client_id = tk.StringVar(value=csl.get("client_id", ""))
        self.csl_api_key = tk.StringVar(value=csl.get("api_key", ""))
        self.csl_webhook = tk.StringVar(value=csl.get("webhook", WEBHOOK_URL))

        card = tk.Frame(v, bg=CARD)
        card.pack(fill="x", padx=18, pady=6)
        pad = tk.Frame(card, bg=CARD)
        pad.pack(fill="x", padx=16, pady=14)
        tk.Label(pad, text="Device Setting", bg=CARD, fg=TEXT,
                 font=(FONT, 13, "bold")).pack(anchor="w")

        tk.Label(pad, text="Sync Token", bg=CARD, fg=SUBTEXT, font=(FONT, 10)).pack(anchor="w", pady=(12, 0))
        row = tk.Frame(pad, bg=CARD)
        row.pack(fill="x", pady=(2, 10))
        tk.Entry(row, textvariable=self.ngrok_token, bg=BG, fg=TEXT, show="•",
                 insertbackground=TEXT, relief="flat", font=("Menlo", 11)
                 ).pack(side="left", fill="x", expand=True, ipady=6)
        Btn(row, "Save", SLATE, SLATE_H, self.save_token, padx=12).pack(side="left", padx=(8, 0))

        def field(label, var, secret=False):
            tk.Label(pad, text=label, bg=CARD, fg=SUBTEXT, font=(FONT, 10)).pack(anchor="w")
            tk.Entry(pad, textvariable=var, bg=BG, fg=TEXT, insertbackground=TEXT,
                     relief="flat", font=("Menlo", 11), show="•" if secret else "").pack(
                fill="x", ipady=5, pady=(2, 8))

        field("Client ID", self.csl_client_id)
        field("Secret Key", self.csl_api_key, secret=True)

        self.csl_status = tk.Label(
            pad,
            text="",
            bg=CARD, fg=SUBTEXT, font=(FONT, 10),
            wraplength=560, justify="left")
        self.csl_status.pack(anchor="w", pady=(0, 8))
        btnrow = tk.Frame(pad, bg=CARD)
        btnrow.pack(fill="x", pady=(4, 0))
        self.tunnel_btn = Btn(btnrow, "Start Sync", GREEN, GREEN_H,
                              self.start_tunnel, padx=14)
        self.tunnel_btn.pack(side="left")
        Btn(btnrow, "Stop", RED, RED_H, self.stop_tunnel, padx=14).pack(side="left", padx=8)
        Btn(btnrow, "Save", INDIGO, INDIGO_H, self.save_csl, padx=14).pack(side="left")
        return v

    def show_view(self, name):
        self.devices_view.pack_forget()
        self.settings_view.pack_forget()
        view = {"Devices": self.devices_view, "Settings": self.settings_view}.get(name)
        if view is None:           # "Logs" -> focus the console
            self.devices_view.pack(fill="both", expand=True)
            self.console.focus_set()
            name = "Devices"
        else:
            view.pack(fill="both", expand=True)
        for n, lbl in self.nav_items.items():
            active = (n == name)
            lbl.config(bg=INDIGO if active else SIDEBAR,
                       fg="white" if active else SUBTEXT,
                       font=(FONT, 12, "bold" if active else "normal"))
        if name == "Settings":
            self.refresh_tunnel_status()

    # ---- helpers ----
    def log(self, msg):
        self.console.insert("end", msg + "\n")
        self.console.see("end")

    def update_counter(self):
        running = sum(1 for r in self.rows if r.monitor_active or r.listen_active)
        total = len(self.rows)
        self.counter.config(text=f"{running} / {total}",
                            fg="#bbf7d0" if running else SUBTEXT,
                            bg="#14532d" if running else MUTED_BG)
        self.side_count.config(text=f"{total} device(s) connected")

    def notify(self, msg, color=INDIGO, ms=3500):
        self.log("• " + msg)
        if getattr(self, "_toast", None) and self._toast.winfo_exists():
            self._toast.destroy()
        self._toast = tk.Label(self.root, text=msg, bg=color, fg="white",
                               font=(FONT, 11, "bold"), padx=16, pady=8)
        self._toast.place(relx=0.5, y=6, anchor="n")
        self.root.after(ms, lambda: self._toast.winfo_exists() and self._toast.destroy())

    def browse(self):
        path = filedialog.askopenfilename(initialdir=HERE,
                                          filetypes=[("Python", "*.py"), ("All", "*.*")])
        if path:
            self.script_path.set(path)

    def refresh(self):
        # remember which devices were active, to resume them after rebuild
        was_monitor = {r.serial for r in self.rows if r.monitor_active}
        was_listen = {r.serial for r in self.rows if r.listen_active}
        for child in self.list_frame.winfo_children():
            child.destroy()
        self.rows = []
        devices = list_devices()
        if not devices:
            self.notify("No devices found — connect via USB, IP, or QR.")
            self.update_counter()
            return
        for serial, model, state in devices:
            row = DeviceRow(self.list_frame, self, serial, model, state)
            row.pack(fill="x")
            self.rows.append(row)
            if serial in was_monitor:      # device still here -> resume
                row.play()
            elif serial in was_listen:
                row.listen()
        self.log(f"↻ Found {len(devices)} device(s)")
        self.update_counter()

    def connect_device(self):
        target = self.connect_ip.get().strip()
        if not target or target.endswith(":"):
            self.notify("Enter a full IP:port", color=RED)
            return
        out = adb("connect", target).strip()
        if "connected" in out.lower():
            self.notify(f"Connected {target}", color=GREEN)
            self.refresh()
        else:
            self.notify(f"Connect failed: {out}", color=RED)

    def disconnect_all(self):
        for r in self.rows:
            if r.proc and r.proc.poll() is None:
                r.stop()
        adb("disconnect")
        self.notify("Disconnected all WiFi devices", color=PURPLE_H)
        self.refresh()

    def run_all(self):
        for r in self.rows:
            if not r.monitor_active:
                r.play()

    # ---- per-device credentials ----
    def device_creds(self, serial):
        return self.settings.get("devices", {}).get(serial, {})

    def save_device_creds(self, serial, username, password):
        dev = self.settings.setdefault("devices", {}).setdefault(serial, {})
        dev["username"], dev["password"] = username, password
        save_settings(self.settings)

    def set_device_value(self, serial, key, value):
        self.settings.setdefault("devices", {}).setdefault(serial, {})[key] = value
        save_settings(self.settings)

    # ---- CSL payment gateway ----
    def _current_public_url(self):
        return self.public_url or detect_running_tunnel()

    def use_ngrok_webhook(self):
        url = self._current_public_url()
        if not url:
            self.notify("Start Sync first", color=RED)
            return
        self.csl_webhook.set(url)
        self.notify("Sync connection ready", color=GREEN)

    def _sync_webhook_to_ngrok(self, url):
        """Auto-fill the CSL webhook with the public URL if it's empty or was
        already pointing at a (stale) ngrok URL."""
        if not hasattr(self, "csl_webhook"):
            return
        cur = self.csl_webhook.get().strip()
        if not cur or "ngrok" in cur:
            self.csl_webhook.set(url)

    def save_csl(self):
        webhook = self._current_public_url() or self.csl_webhook.get().strip()
        if webhook:
            self.csl_webhook.set(webhook)

        cfg = {
            "api_url": CSL_API_URL,
            "client_id": self.csl_client_id.get().strip(),
            "api_key": self.csl_api_key.get().strip(),
            "webhook": webhook,
        }
        self.settings["csl"] = cfg
        save_settings(self.settings)
        if not all((cfg["api_url"], cfg["client_id"], cfg["api_key"])):
            self.csl_status.config(text="Saved. Fill Client ID and Secret Key.",
                                   fg=SUBTEXT)
            return
        if not cfg["webhook"]:
            self.csl_status.config(
                text="Saved. Start Sync, then press Save again.",
                fg=SUBTEXT)
            return
        if not cfg["webhook"].startswith(("http://", "https://")):
            self.csl_status.config(text="Sync connection is not ready.",
                                   fg=RED)
            return
        self.csl_status.config(text="Saving settings…", fg=INDIGO)

        def worker():
            import csl_client
            try:
                ok, msg = csl_client.setup_webhook(
                    cfg["api_url"], cfg["client_id"], cfg["api_key"], cfg["webhook"])
            except Exception as e:
                ok, msg = False, str(e)

            def done():
                display_msg = "Saved successfully." if ok else "Save failed: " + msg
                self.csl_status.config(text=display_msg, fg=GREEN if ok else RED)
                self.notify(("Settings saved" if ok else "Settings save failed: " + msg),
                            color=GREEN if ok else RED)
                self.log(f"[sync] {'OK' if ok else 'FAIL'} {msg}")
            self.root.after(0, done)

        threading.Thread(target=worker, daemon=True).start()

    # ---- ngrok / API ----
    def save_token(self):
        self.settings["ngrok_token"] = self.ngrok_token.get().strip()
        save_settings(self.settings)
        self.notify("Sync token saved", color=GREEN)

    def copy_url(self):
        if self.public_url:
            self.root.clipboard_clear()
            self.root.clipboard_append(self.public_url)
            self.notify("URL copied to clipboard", color=GREEN)

    def _ensure_api(self):
        # run Flask in-process (works inside a frozen .app/.exe, unlike spawning
        # a python subprocess which would re-launch the bundled app instead).
        if getattr(self, "_api_started", False):
            return
        import api
        threading.Thread(
            target=lambda: api.app.run(host="127.0.0.1", port=API_PORT,
                                       threaded=True, use_reloader=False),
            daemon=True).start()
        self._api_started = True
        self.log("Sync service started")

    def start_tunnel(self):
        tok = self.ngrok_token.get().strip()
        if not tok:
            self.notify("Enter your Sync Token first", color=RED)
            self.show_view("Settings")
            return
        self.save_token()
        self.tunnel_btn.set_enabled(False)
        self.url_var.set("starting…")

        def worker():
            try:
                import time
                import shutil
                from pyngrok import ngrok, conf
                # use the bundled/PATH ngrok instead of letting pyngrok download one
                _ng = shutil.which("ngrok")
                if _ng:
                    conf.get_default().ngrok_path = _ng
                # already online? just adopt and display it as running.
                existing = detect_running_tunnel()
                if existing:
                    self.public_url = existing
                    self._ensure_api()

                    def already():
                        self.url_var.set(existing)
                        self.side_tunnel.config(text="● Sync: on", fg=GREEN)
                        self._sync_webhook_to_ngrok(existing)
                        self.notify("Sync already running", color=GREEN)
                        self.tunnel_btn.set_enabled(True)
                    self.root.after(0, already)
                    return

                ngrok.set_auth_token(tok)
                self._ensure_api()
                time.sleep(1.5)                      # let Flask bind the port
                tunnel = ngrok.connect(str(API_PORT), "http")
                self.public_url = tunnel.public_url.replace("http://", "https://")

                def ok():
                    self.url_var.set(self.public_url)
                    self.side_tunnel.config(text="● Sync: on", fg=GREEN)
                    self._sync_webhook_to_ngrok(self.public_url)
                    self.notify("Sync started", color=GREEN)
                    self.tunnel_btn.set_enabled(True)
                self.root.after(0, ok)
            except Exception as e:
                msg = str(e)
                if "ERR_NGROK_107" in msg or "authtoken" in msg.lower():
                    short = "Invalid Sync Token"
                elif "ERR_NGROK_108" in msg or "simultaneous" in msg.lower():
                    short = "Sync session limit reached"
                elif "ERR_NGROK_334" in msg or "already online" in msg.lower():
                    short = "Sync is already running — click Stop, then Start again"
                else:
                    short = msg.splitlines()[0][:80] if msg else "unknown error"

                def fail():
                    self.url_var.set(short)
                    self.log("[sync] " + msg.replace("\\n", " "))   # full detail in console
                    self.notify("Sync failed: " + short, color=RED, ms=6000)
                    self.tunnel_btn.set_enabled(True)
                self.root.after(0, fail)
            finally:
                # clean up the half-started ngrok process on failure
                try:
                    from pyngrok import ngrok as _ng
                    if not self.public_url:
                        _ng.kill()
                except Exception:
                    pass

        threading.Thread(target=worker, daemon=True).start()

    def refresh_tunnel_status(self):
        """If an ngrok tunnel is already running, reflect it in the UI."""
        def worker():
            url = detect_running_tunnel()

            def apply():
                if url:
                    self.public_url = url
                    self.url_var.set(url)
                    self.side_tunnel.config(text="● Sync: on", fg=GREEN)
                elif not self.public_url:
                    self.side_tunnel.config(text="● Sync: off", fg=SUBTEXT)
            self.root.after(0, apply)
        threading.Thread(target=worker, daemon=True).start()

    def stop_tunnel(self):
        def worker():
            try:
                from pyngrok import ngrok
                ngrok.kill()
            except Exception:
                pass
            # the API runs as an in-process daemon thread; it stops with the app
            self.public_url = None

            def done():
                self.url_var.set("")
                self.side_tunnel.config(text="● Sync: off", fg=SUBTEXT)
                self.tunnel_btn.set_enabled(True)   # allow Start again
                self.notify("Sync stopped", color=PURPLE_H)
            self.root.after(0, done)

        threading.Thread(target=worker, daemon=True).start()

    def show_qr_dialog(self):
        import qrcode
        from PIL import ImageTk

        name, password = qr_connect.make_credentials()
        payload = qr_connect.qr_payload(name, password)

        win = tk.Toplevel(self.root, bg=CARD)
        win.title("Pair with QR")
        win.geometry("360x500")
        win.configure(bg=CARD)

        tk.Label(win, text="Scan to pair", bg=CARD, fg=TEXT,
                 font=(FONT, 15, "bold")).pack(pady=(20, 2))
        tk.Label(win, text="Wireless debugging → Pair device with QR code",
                 bg=CARD, fg=SUBTEXT, font=(FONT, 10)).pack(pady=(0, 14))

        img = qrcode.make(payload, box_size=8, border=2).convert("RGB")
        photo = ImageTk.PhotoImage(img)
        ql = tk.Label(win, image=photo, bg="white", bd=10, relief="flat")
        ql.image = photo
        ql.pack(pady=4)

        status = tk.Label(win, text="Waiting for scan…", bg=CARD, fg=INDIGO,
                          font=(FONT, 11, "bold"), wraplength=300, justify="center")
        status.pack(pady=16)

        def on_status(msg):
            self.root.after(0, lambda: (status.config(text=msg), self.log("[QR] " + msg)))

        def worker():
            try:
                serial = qr_connect.wait_and_pair(name, password, on_status=on_status)
                self.root.after(0, lambda: status.config(text=f"✅ {serial}", fg=GREEN))
                self.root.after(0, lambda: self.notify(f"QR paired {serial}", color=GREEN))
                self.root.after(0, self.refresh)
                self.root.after(2200, win.destroy)
            except Exception as e:
                self.root.after(0, lambda: status.config(text=f"❌ {e}", fg=RED))
                self.root.after(0, lambda: self.notify(f"QR failed: {e}", color=RED))

        threading.Thread(target=worker, daemon=True).start()


if __name__ == "__main__":
    root = tk.Tk()
    App(root)
    root.mainloop()
