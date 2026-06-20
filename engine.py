"""
Headless monitoring engine for the Electron backend.

Owns per-device monitor threads and reuses bcel (device polling) + csl_client
(gateway signing). Emits live events (log lines, device status, synced
transactions) to subscribers for the UI's SSE stream.
"""
import os
import json
import time
import uuid
import queue
import threading
import subprocess
import collections

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
APP_VERSION = "1.0.3"
MONITOR_INTERVAL = 60
# Fully restart the BCEL app (stop + start) every Nth poll cycle, per device.
# This clears a stale/expired session and a frozen WebView instead of letting
# them pile up between the lightweight resume-polls. At a 60s interval, 30 cycles
# ≈ 30 minutes. The cycle count is tracked globally per device (Engine._cycles)
# so it survives a monitor stop/start instead of resetting each time.
FRESH_RESTART_CYCLES = 30
# How many consecutive "not reachable" cycles to tolerate before stopping a
# monitor — lets a brief Wi-Fi/internet drop recover instead of killing it.
OFFLINE_TOLERANCE = 3
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


def adb(*args, timeout=15):
    # Always bound the call: a flaky Wi-Fi device can wedge the adb server, and
    # without a timeout subprocess.run would hang the monitor thread AND the
    # /api/devices UI call forever. On timeout/failure return "" (treated as "no
    # output") so callers degrade gracefully instead of blocking.
    try:
        return subprocess.run(["adb", *args], capture_output=True, text=True,
                               creationflags=_NO_WINDOW, timeout=timeout).stdout
    except subprocess.TimeoutExpired:
        return ""
    except Exception:
        return ""


def list_devices():
    out = adb("devices", "-l", timeout=10)
    res = []
    for line in out.splitlines()[1:]:
        line = line.strip()
        if not line or "_adb-tls-" in line:
            continue
        parts = line.split()
        if len(parts) < 2:                    # malformed line — skip, don't crash
            continue
        serial, state = parts[0], parts[1]
        model = next((p.split(":", 1)[1].replace("_", " ")
                      for p in parts[2:] if p.startswith("model:")), "Android device")
        res.append({"serial": serial, "model": model, "state": state})
    return res


def device_online(serial):
    return any(d["serial"] == serial and d["state"] == "device"
               for d in list_devices())


def device_state(serial):
    """The adb state for a serial: 'device', 'unauthorized', 'offline', or None
    (not present). Only 'device' is actually usable."""
    for d in list_devices():
        if d["serial"] == serial:
            return d["state"]
    return None


# human-readable reason a device isn't usable, keyed by adb state
_STATE_REASON = {
    "unauthorized": "unauthorized — tap Allow (USB debugging) on the phone, then Start again",
    "offline": "offline — unplug/replug or reconnect the device",
    None: "not connected",
}


def source_account(t):
    """Extract the (from_account, from_name) the money was transferred FROM,
    matching the gateway's handleAutoMateTransaction logic (raw[]-based)."""
    # Preferred: extracted from the list-row text (reliable on every device).
    if t.get("from_account") or t.get("from_name"):
        return (t.get("from_account", "") or "").strip(), (t.get("from_name", "") or "").strip()
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
        self._cycles = {}         # serial -> current poll-cycle count (global, all devices)
        self.transactions = []    # synced transactions (oldest first)
        self._subs = []           # SSE subscriber queues
        # serializes settings mut-and-save across the per-device monitor threads
        # (and Flask request threads) so concurrent writers can't corrupt the file.
        self._slock = threading.RLock()
        # global guard against ever posting the same bank reference twice (across
        # cycles, fresh restarts, and multiple devices). Bounded, newest-last, and
        # persisted (a tail) to settings so it also survives a full app restart.
        self._sent_refs = collections.OrderedDict()
        self._sent_lock = threading.Lock()
        self._SENT_MAX = 10000          # kept in memory
        self._SENT_PERSIST = 3000       # kept on disk
        for k in self.settings.get("sent_refs", []):
            self._sent_refs[k] = True

    def _save(self):
        with self._slock:
            save_settings(self.settings)

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

    # ---------- settings (global) ----------
    def get_settings(self):
        return {
            "ngrok_token_set": bool(self.settings.get("ngrok_token")),
            "default_api_url": GATEWAY_API_URL,
        }

    # ---------- setting sets (named gateway profiles) ----------
    def sets(self):
        """All gateway profiles (secrets redacted to a has_secret flag)."""
        out = []
        for sid, s in self.settings.get("sets", {}).items():
            out.append({
                "id": sid,
                "name": s.get("name", ""),
                "client_id": s.get("client_id", ""),
                "has_secret": bool(s.get("api_key")),
                "api_url": s.get("api_url", "") or GATEWAY_API_URL,
            })
        return out

    def save_set(self, set_id, name, client_id, secret, api_url):
        """Create (blank set_id) or update a gateway profile. Returns its id.
        A blank secret on update keeps the stored one."""
        with self._slock:
            sets = self.settings.setdefault("sets", {})
            if not set_id:
                set_id = uuid.uuid4().hex[:8]
            s = sets.setdefault(set_id, {})
            s["name"] = (name or s.get("name") or "Untitled").strip()
            s["client_id"] = (client_id or "").strip()
            if secret:
                s["api_key"] = secret.strip()
            s["api_url"] = (api_url or "").strip()
            save_settings(self.settings)
        return set_id

    def delete_set(self, set_id):
        with self._slock:
            self.settings.get("sets", {}).pop(set_id, None)
            # unassign any device that pointed to it
            for dev in self.settings.get("devices", {}).values():
                if dev.get("set") == set_id:
                    dev.pop("set", None)
            save_settings(self.settings)
        return {"ok": True}

    def device_set(self, serial):
        """The gateway profile assigned to a device, or None."""
        sid = self.device_creds(serial).get("set")
        return self.settings.get("sets", {}).get(sid) if sid else None

    def assign_device_set(self, serial, set_id):
        with self._slock:
            dev = self.settings.setdefault("devices", {}).setdefault(serial, {})
            if set_id:
                dev["set"] = set_id
            else:
                dev.pop("set", None)
            save_settings(self.settings)
        return {"ok": True}

    def setup_set_webhook(self, set_id):
        """Register the current public Sync URL as this set's webhook with the
        gateway (POST /bcel/setup), signed with the set's own credentials."""
        s = self.settings.get("sets", {}).get(set_id)
        if not s:
            return {"ok": False, "message": "Set not found"}
        cid = (s.get("client_id") or "").strip()
        key = (s.get("api_key") or "").strip()
        if not (cid and key):
            return {"ok": False, "message": "Set is missing Client ID / Secret Key"}
        # The webhook/Sync URL is optional — call /bcel/setup regardless so the
        # credentials still get verified. Use a public URL if one happens to be up.
        hook = (self._detect_tunnel() or self.settings.get("webhook") or s.get("webhook") or "").strip()
        api_url = (s.get("api_url") or "").strip() or GATEWAY_API_URL
        if hook:
            with self._slock:
                s["webhook"] = hook
                save_settings(self.settings)
        try:
            ok, msg = csl_client.setup_webhook(api_url, cid, key, hook)
            self.log(f"/bcel/setup [{s.get('name')}] → {msg}")
            return {"ok": ok, "message": msg, "webhook": hook}
        except Exception as e:
            return {"ok": False, "message": str(e)}

    def device_creds(self, serial):
        return self.settings.get("devices", {}).get(serial, {})

    def save_device_creds(self, serial, username=None, password=None):
        with self._slock:
            dev = self.settings.setdefault("devices", {}).setdefault(serial, {})
            if username is not None:
                dev["username"] = username
            if password is not None:
                dev["password"] = password
            save_settings(self.settings)

    def set_last_ref(self, serial, ref):
        with self._slock:
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
                "set": self.device_creds(s).get("set", ""),
                "cycle": self._cycles.get(s, 0),
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
        # don't spin up a monitor for a device adb can't actually drive
        st = device_state(serial)
        if st != "device":
            reason = _STATE_REASON.get(st, f"state '{st}'")
            self._set_status(serial, "unauthorized" if st == "unauthorized" else "disconnected")
            self.log(f"⚠ {serial}: {reason}")
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
        offline = 0                              # consecutive non-'device' readings
        while m["active"]:
            st = device_state(serial)
            if st != "device":
                # 'unauthorized' needs a human (tap Allow) — stop right away.
                # 'offline'/missing can be a transient Wi-Fi/adb blip, so tolerate
                # a few consecutive misses before giving up, and auto-recover if it
                # comes back. This keeps a brief internet drop from killing monitors.
                if st == "unauthorized":
                    self._set_status(serial, "unauthorized")
                    m["active"] = False
                    self.log(f"⚠ {serial}: {_STATE_REASON['unauthorized']} — monitor stopped")
                    return
                offline += 1
                self._set_status(serial, "reconnecting")
                if offline >= OFFLINE_TOLERANCE:
                    self._set_status(serial, "disconnected")
                    m["active"] = False
                    self.log(f"⚠ {serial}: {_STATE_REASON.get(st, 'offline')} — monitor stopped after {offline} tries")
                    return
                # a Wi-Fi device (ip:port) usually needs an explicit reconnect
                # after a drop — try to re-establish it before the next check
                if ":" in serial:
                    adb("connect", serial, timeout=10)
                self.log(f"… {serial}: not reachable ({offline}/{OFFLINE_TOLERANCE}) — retrying")
                for _ in range(MONITOR_INTERVAL):
                    if not m["active"]:
                        break
                    time.sleep(1)
                continue
            offline = 0                          # reachable again -> reset
            try:
                last_ref = self.device_creds(serial).get("last_ref") or None
                # Global per-device cycle counter: a fresh app restart happens at
                # count 0 (first poll, and every FRESH_RESTART_CYCLES thereafter)
                # to drop any expired session / frozen WebView before reading.
                count = self._cycles.get(serial, 0)
                fresh = (count == 0)
                if fresh:
                    self.log(f"↻ {serial}: fresh app restart (cycle {count}/{FRESH_RESTART_CYCLES})")
                self.log(f"⟳ {serial}: refreshing messages… (cycle {count}/{FRESH_RESTART_CYCLES})")
                res = bcel.poll_messages(serial, last_ref, pwd, user, fresh=fresh,
                                         log=lambda msg: self.log(f"   {serial}: {msg}"))
                new = res.get("new") or []
                advance = True
                if new:
                    advance = self._send(serial, new)
                else:
                    self.log(f"· {serial}: no new transactions")
                # Only move the watermark forward if the send actually succeeded
                # (or there was nothing to send). On a transient failure we keep
                # the old watermark so the same transactions are retried next cycle
                # instead of being skipped and lost.
                if res.get("last_ref") and advance:
                    self.set_last_ref(serial, res["last_ref"])
                # advance the global cycle, wrapping back to 0 (fresh) every N cycles
                self._cycles[serial] = (count + 1) % FRESH_RESTART_CYCLES
            except Exception as e:
                # Don't hard-stop on a single error (a Wi-Fi drop mid-poll throws):
                # just log and let the tolerant top-of-loop check decide whether the
                # device is really gone over the next few cycles.
                self.log(f"✗ {serial}: {e}")
            for _ in range(MONITOR_INTERVAL):
                if not m["active"]:
                    break
                time.sleep(1)
        self._set_status(serial, "idle")
        self.log(f"■ {serial}: monitor stopped")

    # ---------- sync (public tunnel via ngrok) ----------
    def save_token(self, token):
        with self._slock:
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

    @staticmethod
    def _dedup_key(t):
        """Identity used to guard against double-posting. Real bank references
        (bill_no / FQR…/FAC…) are globally unique, so they dedup across ALL
        devices — this also stops two phones on the same account from both
        forwarding the same transfer. The time-based fallback ref ("HH:MM:SS|type")
        is NOT unique, so it's scoped per device + amount instead."""
        ref = (t.get("ref") or t.get("bill_no") or "").strip()
        if ref and "|" not in ref:
            return ref
        amt = t.get("amount_in") or t.get("amount") or ""
        return f"{t.get('serial')}|{ref}|{amt}"

    def _send(self, serial, new):
        s = self.device_set(serial)
        cid = (s.get("client_id") or "").strip() if s else ""
        key = (s.get("api_key") or "").strip() if s else ""
        api_url = ((s.get("api_url") or "").strip() if s else "") or GATEWAY_API_URL
        txns = [{**t, "serial": serial} for t in new]
        # GLOBAL duplicate guard: drop any transaction whose reference was already
        # posted (by this device on an earlier cycle, after a fresh restart, or by
        # another device sharing the same account).
        with self._sent_lock:
            kept = []
            for t in txns:
                k = self._dedup_key(t)
                if k in self._sent_refs:
                    self.log(f"⚠ {serial}: skipped duplicate transaction (ref {t.get('ref') or t.get('bill_no')})")
                    continue
                kept.append(t)
            txns = kept
        if not txns:
            return True            # nothing new (or all already sent) -> safe to advance
        # Newer app builds insert a spurious brand element at raw[2] ("OneBank Kid"
        # or a duplicate "OneBank"), pushing the real "OneBank" to raw[3] and
        # shifting every later index — which breaks the gateway's fixed raw[]
        # lookups. The real brand at raw[3] is the tell: drop raw[2] until raw[]
        # matches the canonical format ([MAIN, BCEL One, OneBank, MESSAGE, ...]).
        for t in txns:
            raw = t.get("raw")
            if isinstance(raw, list):
                raw = list(raw)
                while len(raw) > 3 and raw[3] == "OneBank":
                    del raw[2]
                t["raw"] = raw
        # Not configured -> DON'T advance the watermark: keep these transactions
        # pending so they're sent once a valid set is assigned (no silent loss).
        if not s:
            self.log(f"⚠ {serial}: no setting set assigned — held (assign one on the device card)")
            return False
        if not (cid and key):
            self.log(f"⚠ {serial}: set '{s.get('name')}' is missing credentials — held")
            return False
        try:
            ok, msg, transient = csl_client.post_transactions(api_url, cid, key, txns, timeout=10)
            self.log(f"→ {serial}: synced {len(txns)} transaction(s): {msg}")
            if ok:
                # remember these references so they can never be posted again
                with self._sent_lock:
                    for t in txns:
                        self._sent_refs[self._dedup_key(t)] = True
                    while len(self._sent_refs) > self._SENT_MAX:
                        self._sent_refs.popitem(last=False)   # evict oldest
                    tail = list(self._sent_refs.keys())[-self._SENT_PERSIST:]
                # persist a tail to disk (outside _sent_lock to keep lock order)
                with self._slock:
                    self.settings["sent_refs"] = tail
                    save_settings(self.settings)
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
                return True
            # gateway reachable but rejected the batch (4xx) -> advance so one bad
            # record can't block everything; a transient/5xx/network error -> retry.
            if transient:
                self.log(f"↻ {serial}: gateway unreachable — will retry next cycle")
                return False
            return True
        except Exception as e:
            self.log(f"⚠ {serial}: sync failed: {e} — will retry next cycle")
            return False
