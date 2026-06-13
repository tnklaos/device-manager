"""
HTTP API for BCEL One automation.

Endpoints:
  GET  /health                      -> {"status":"ok"}
  GET  /devices                     -> connected devices
  POST /qr                          -> create a QR with amount; returns the QR string
       body: {"serial","amount","description","password"?,"submit"?}
  POST /messages                    -> scrape transaction messages
       body: {"serial","password"?,"max_scrolls"?}

Concurrency: a per-device lock serializes requests to the SAME serial (they
share one screen), while different serials run in parallel. A request that
would block on a busy device returns 409 unless you pass "wait": true.

Run:  ./venv/bin/python api.py        (listens on http://127.0.0.1:8000)
"""
import subprocess
import threading
from collections import defaultdict
from flask import Flask, request, jsonify

import bcel

app = Flask(__name__)

# one lock per device serial
_locks = defaultdict(threading.Lock)
_locks_guard = threading.Lock()


def device_lock(serial):
    with _locks_guard:
        return _locks[serial]


def list_devices():
    out = subprocess.run(["adb", "devices", "-l"], capture_output=True, text=True,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    devices = []
    for line in out.splitlines()[1:]:
        line = line.strip()
        if not line or "_adb-tls-" in line:
            continue
        parts = line.split()
        model = next((p.split(":", 1)[1] for p in parts[2:] if p.startswith("model:")), "")
        devices.append({"serial": parts[0], "state": parts[1], "model": model})
    return devices


@app.get("/health")
def health():
    return jsonify(status="ok")


@app.get("/devices")
def devices():
    return jsonify(devices=list_devices())


def _run_locked(serial, wait, fn):
    """Acquire the device lock (or 409 if busy and not waiting), run fn()."""
    lock = device_lock(serial)
    acquired = lock.acquire(blocking=wait)
    if not acquired:
        return jsonify(error="device busy", serial=serial), 409
    try:
        return fn()
    except Exception as e:
        return jsonify(error=str(e), serial=serial), 500
    finally:
        lock.release()


@app.post("/qr")
def create_qr():
    body = request.get_json(force=True, silent=True) or {}
    serial = body.get("serial")
    if not serial:
        return jsonify(error="missing 'serial'"), 400
    amount = body.get("amount", "")
    description = body.get("description", "")
    password = body.get("password", "")
    username = body.get("username", "")
    submit = bool(body.get("submit", True))
    wait = bool(body.get("wait", True))
    if amount in ("", None):
        return jsonify(error="missing 'amount'"), 400

    lock = device_lock(serial)
    if not lock.acquire(blocking=wait):
        return jsonify(error="device busy", serial=serial), 409

    logs = []
    try:
        res = bcel.create_qr(serial, amount, description, password, username=username,
                             submit=submit, go_home_after=False, log=logs.append)
        res["log"] = logs
    except Exception as e:
        lock.release()
        return jsonify(error=str(e), serial=serial, log=logs), 500

    # Respond now; finish returning the phone to the home page in the
    # background. We keep holding the device lock until go_home completes, so
    # the next request to this serial waits for the phone to be ready.
    res["home"] = "pending"

    def finish_home():
        try:
            import uiautomator2 as u2
            bcel.go_home(u2.connect(serial))
        except Exception:
            pass
        finally:
            lock.release()

    threading.Thread(target=finish_home, daemon=True).start()
    return jsonify(res)


@app.post("/messages")
def messages():
    body = request.get_json(force=True, silent=True) or {}
    serial = body.get("serial")
    if not serial:
        return jsonify(error="missing 'serial'"), 400
    password = body.get("password", "")
    username = body.get("username", "")
    max_scrolls = int(body.get("max_scrolls", 8))
    wait = bool(body.get("wait", True))

    def job():
        data = bcel.get_messages(serial, password, username=username,
                                 max_scrolls=max_scrolls, log=lambda *_: None)
        return jsonify(serial=serial, count=len(data), messages=data)

    return _run_locked(serial, wait, job)


if __name__ == "__main__":
    # threaded=True so different devices are handled in parallel;
    # the per-device locks keep same-device requests serialized.
    app.run(host="127.0.0.1", port=8000, threaded=True)
