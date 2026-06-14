"""
Headless monitoring engine for the Electron backend.

Owns per-device monitor threads and reuses bcel (device polling) + csl_client
(gateway signing). Emits live events (log lines, device status, synced
transactions) to subscribers for the UI's SSE stream.
"""
import os
import json
import time
import queue
import threading
import subprocess

import bcel
import csl_client

import sys

HERE = os.path.dirname(os.path.abspath(__file__))

# Use the bundled adb/ngrok when frozen, and fall back to common install
# locations when launched from Finder/Explorer (minimal PATH).
if getattr(sys, "frozen", False):
    _bb = os.path.join(getattr(sys, "_MEIPASS", HERE), "bin")
    if os.path.isdir(_bb):
        os.environ["PATH"] = _bb + os.pathsep + os.environ.get("PATH", "")
for _p in ("/opt/homebrew/bin", "/usr/local/bin",
           os.path.expanduser("~/Library/Android/sdk/platform-tools"),
           r"C:\platform-tools", r"C:\ngrok"):
    if os.path.isdir(_p) and _p not in os.environ.get("PATH", ""):
        os.environ["PATH"] = os.environ.get("PATH", "") + os.pathsep + _p

if getattr(sys, "frozen", False):
    # writable per-user location (the app bundle is read-only)
    _cfg = os.path.join(os.path.expanduser("~"), ".device-manager")
    os.makedirs(_cfg, exist_ok=True)
    SETTINGS_FILE = os.path.join(_cfg, "settings.json")
else:
    SETTINGS_FILE = os.path.join(HERE, "settings.json")
GATEWAY_API_URL = "https://paymentgateway.108pay.co"
MONITOR_INTERVAL = 60
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def load_settings():
    try:
        with open(SETTINGS_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def save_settings(data):
    with open(SETTINGS_FILE, "w") as f:
        json.dump(data, f, indent=2)


def adb(*args):
    return subprocess.run(["adb", *args], capture_output=True, text=True,
                          creationflags=_NO_WINDOW).stdout


def list_devices():
    out = adb("devices", "-l")
    res = []
    for line in out.splitlines()[1:]:
        line = line.strip()
        if not line or "_adb-tls-" in line:
            continue
        parts = line.split()
        serial, state = parts[0], parts[1]
        model = next((p.split(":", 1)[1].replace("_", " ")
                      for p in parts[2:] if p.startswith("model:")), "Android device")
        res.append({"serial": serial, "model": model, "state": state})
    return res


def device_online(serial):
    return any(d["serial"] == serial and d["state"] == "device"
               for d in list_devices())


def source_account(t):
    """Extract the (from_account, from_name) the money was transferred FROM,
    matching the gateway's handleAutoMateTransaction logic (raw[]-based)."""
    raw = t.get("raw", []) or []
    raw4 = raw[4] if len(raw) > 4 else ""
    if "|" in raw4:                       # QR / LMPS pipe statement
        parts = raw4.split("|")
        ttype = parts[0].strip().upper().replace(" ", "")
        idx = 5 if "ONEPAY" in ttype else 2
        return (parts[idx].strip() if len(parts) > idx else ""), ""
    # regular transfer-in: raw[5] = "NAME\naccount-number" (fallback raw[9])
    raw5 = raw[5] if len(raw) > 5 else ""
    sel = raw5.split("\n")
    if len(sel) > 1:
        return sel[1].strip(), sel[0].strip()
    return (raw[9].strip() if len(raw) > 9 else ""), ""


class Engine:
    def __init__(self):
        self.settings = load_settings()
        self._monitors = {}       # serial -> {"active": bool, "thread": Thread}
        self._status = {}         # serial -> status string
        self.transactions = []    # synced transactions (oldest first)
        self._subs = []           # SSE subscriber queues

    # ---------- event bus (SSE) ----------
    def subscribe(self):
        q = queue.Queue()
        self._subs.append(q)
        return q

    def unsubscribe(self, q):
        if q in self._subs:
            self._subs.remove(q)

    def emit(self, kind, data):
        evt = {"kind": kind, "data": data, "ts": time.time()}
        for q in list(self._subs):
            try:
                q.put_nowait(evt)
            except Exception:
                pass

    def log(self, msg):
        self.emit("log", {"msg": msg})

    # ---------- settings ----------
    def get_settings(self):
        gw = self.settings.get("csl", {})
        return {
            "client_id": gw.get("client_id", ""),
            "has_secret": bool(gw.get("api_key")),
            "ngrok_token_set": bool(self.settings.get("ngrok_token")),
        }

    def save_gateway(self, client_id, secret_key):
        gw = self.settings.setdefault("csl", {})
        gw["client_id"] = client_id or ""
        if secret_key:
            gw["api_key"] = secret_key
        save_settings(self.settings)

    def setup_gateway(self):
        """Register the webhook with the gateway (POST /bcel/setup), signed with
        the saved credentials. The webhook is the current public Sync URL."""
        gw = self.settings.get("csl", {})
        cid = (gw.get("client_id") or "").strip()
        key = (gw.get("api_key") or "").strip()
        if not (cid and key):
            return {"ok": False, "message": "Enter Client ID and Secret Key first"}
        webhook = self._detect_tunnel() or gw.get("webhook", "")
        if not webhook:
            return {"ok": False, "message": "Start Sync first to get a public webhook URL"}
        gw["webhook"] = webhook
        save_settings(self.settings)
        try:
            import csl_client
            ok, msg = csl_client.setup_webhook(GATEWAY_API_URL, cid, key, webhook)
            self.log(f"/bcel/setup: {msg}")
            return {"ok": ok, "message": msg, "webhook": webhook}
        except Exception as e:
            return {"ok": False, "message": str(e)}

    def device_creds(self, serial):
        return self.settings.get("devices", {}).get(serial, {})

    def save_device_creds(self, serial, username=None, password=None):
        dev = self.settings.setdefault("devices", {}).setdefault(serial, {})
        if username is not None:
            dev["username"] = username
        if password is not None:
            dev["password"] = password
        save_settings(self.settings)

    def set_last_ref(self, serial, ref):
        self.settings.setdefault("devices", {}).setdefault(serial, {})["last_ref"] = ref
        save_settings(self.settings)

    # ---------- devices ----------
    def devices(self):
        out = []
        for d in list_devices():
            s = d["serial"]
            out.append({
                **d,
                "monitoring": self._monitors.get(s, {}).get("active", False),
                "status": self._status.get(s, "idle"),
                "has_creds": bool(self.device_creds(s).get("password")),
                "last_ref": self.device_creds(s).get("last_ref", ""),
                "username": self.device_creds(s).get("username", ""),
            })
        return out

    def transactions_list(self):
        return self.transactions[-500:]

    def clear_transactions(self):
        self.transactions = []

    # ---------- monitor control ----------
    def start(self, serial):
        if self._monitors.get(serial, {}).get("active"):
            return
        m = {"active": True}
        self._monitors[serial] = m
        m["thread"] = threading.Thread(target=self._loop, args=(serial, m), daemon=True)
        self._set_status(serial, "monitoring")
        self.log(f"▶ {serial}: monitor started")
        m["thread"].start()

    def stop(self, serial):
        m = self._monitors.get(serial)
        if m and m["active"]:
            m["active"] = False
            self._set_status(serial, "stopping")

    def _set_status(self, serial, status):
        self._status[serial] = status
        self.emit("device", {"serial": serial, "status": status})

    def _loop(self, serial, m):
        creds = self.device_creds(serial)
        pwd, user = creds.get("password", ""), creds.get("username", "")
        while m["active"]:
            if not device_online(serial):
                self._set_status(serial, "disconnected")
                m["active"] = False
                self.log(f"⚠ {serial}: disconnected — monitor stopped")
                return
            try:
                last_ref = self.device_creds(serial).get("last_ref") or None
                self.log(f"⟳ {serial}: refreshing messages…")
                res = bcel.poll_messages(serial, last_ref, pwd, user,
                                         log=lambda msg: self.log(f"   {serial}: {msg}"))
                new = res.get("new") or []
                if new:
                    self._send(serial, new)
                else:
                    self.log(f"· {serial}: no new transactions")
                if res.get("last_ref"):
                    self.set_last_ref(serial, res["last_ref"])
            except Exception as e:
                if not device_online(serial):
                    self._set_status(serial, "disconnected")
                    m["active"] = False
                    return
                self.log(f"✗ {serial}: {e}")
            for _ in range(MONITOR_INTERVAL):
                if not m["active"]:
                    break
                time.sleep(1)
        self._set_status(serial, "idle")
        self.log(f"■ {serial}: monitor stopped")

    # ---------- sync (public tunnel via ngrok) ----------
    def save_token(self, token):
        self.settings["ngrok_token"] = token or ""
        save_settings(self.settings)

    def _detect_tunnel(self):
        try:
            import requests
            r = requests.get("http://127.0.0.1:4040/api/tunnels", timeout=2)
            for t in r.json().get("tunnels", []):
                if t.get("public_url", "").startswith("https"):
                    return t["public_url"]
        except Exception:
            pass
        return None

    def sync_status(self):
        url = self._detect_tunnel()
        return {"running": bool(url), "url": url or ""}

    def start_sync(self, token=None):
        token = (token or self.settings.get("ngrok_token") or "").strip()
        if not token:
            return {"ok": False, "error": "No Sync token set"}
        self.save_token(token)
        existing = self._detect_tunnel()
        if existing:
            return {"ok": True, "url": existing}
        try:
            import shutil
            from pyngrok import ngrok, conf
            ng = shutil.which("ngrok")
            if ng:
                conf.get_default().ngrok_path = ng
            ngrok.set_auth_token(token)
            tunnel = ngrok.connect("8000", "http")
            url = tunnel.public_url.replace("http://", "https://")
            self.log(f"Sync started: {url}")
            return {"ok": True, "url": url}
        except Exception as e:
            msg = str(e)
            if "ERR_NGROK_107" in msg or "authtoken" in msg.lower():
                msg = "Invalid Sync token"
            return {"ok": False, "error": msg}

    def stop_sync(self):
        try:
            from pyngrok import ngrok
            ngrok.kill()
        except Exception:
            pass
        self.log("Sync stopped")
        return {"ok": True}

    # ---------- gateway /bcel/setup (register webhook) ----------
    def setup_webhook(self, webhook=None):
        gw = self.settings.get("csl", {})
        cid = (gw.get("client_id") or "").strip()
        key = (gw.get("api_key") or "").strip()
        hook = (webhook or self._detect_tunnel() or gw.get("webhook") or "").strip()
        if not (cid and key):
            return {"ok": False, "message": "Enter Client ID and Secret Key first"}
        if not hook:
            return {"ok": False, "message": "Start Sync first to get a public URL"}
        self.settings.setdefault("csl", {})["webhook"] = hook
        save_settings(self.settings)
        try:
            ok, msg = csl_client.setup_webhook(GATEWAY_API_URL, cid, key, hook)
            self.log(f"/bcel/setup → {msg}")
            return {"ok": ok, "message": msg, "webhook": hook}
        except Exception as e:
            return {"ok": False, "message": str(e)}

    # ---------- device actions ----------
    def mirror(self, serial):
        subprocess.Popen(["scrcpy", "--serial", serial, "--window-title", serial],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=_NO_WINDOW)
        self.log(f"\U0001F5A5 {serial}: mirror launched")

    def disconnect(self, serial):
        self.stop(serial)
        if ":" in serial:
            adb("disconnect", serial)
            self.log(f"⏏ {serial}: disconnected")
            return {"ok": True}
        return {"ok": True, "message": "USB device — unplug to disconnect"}

    # ---------- QR pairing ----------
    def _qr_png(self, payload):
        import io, base64, qrcode
        img = qrcode.make(payload, box_size=8, border=2).convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()

    def start_qr_pair(self):
        import qr_connect
        name, password = qr_connect.make_credentials()
        payload = qr_connect.qr_payload(name, password)

        def worker():
            try:
                serial = qr_connect.wait_and_pair(
                    name, password,
                    on_status=lambda m: self.emit("pair", {"status": m}))
                self.emit("pair", {"status": f"Connected {serial}", "done": True, "serial": serial})
                self.emit("device", {"serial": serial, "status": "idle"})
            except Exception as e:
                self.emit("pair", {"status": str(e), "error": True})
        threading.Thread(target=worker, daemon=True).start()
        return {"payload": payload, "qr_png": self._qr_png(payload)}

    def _send(self, serial, new):
        gw = self.settings.get("csl", {})
        cid = (gw.get("client_id") or "").strip()
        key = (gw.get("api_key") or "").strip()
        txns = [{**t, "serial": serial} for t in new]
        if not (cid and key):
            self.log(f"⚠ {serial}: gateway credentials missing — not sent")
            return
        try:
            ok, msg = csl_client.post_transactions(GATEWAY_API_URL, cid, key, txns, timeout=10)
            self.log(f"→ {serial}: synced {len(txns)} transaction(s): {msg}")
            if ok:
                for t in txns:
                    from_acct, from_name = source_account(t)
                    rec = {"serial": serial, "type": t.get("type", ""),
                           "kind": t.get("kind", ""),
                           "from_account": from_acct,
                           "from_name": from_name,
                           "to_account": t.get("account", ""),
                           "details": t.get("details", ""),
                           "ref": t.get("ref", "") or t.get("bill_no", ""),
                           "amount": t.get("amount_in") or t.get("amount") or "",
                           "time": t.get("time", ""), "synced_at": time.time()}
                    self.transactions.append(rec)
                    self.emit("transaction", rec)
        except Exception as e:
            self.log(f"⚠ {serial}: sync failed: {e}")
