"""
Backend HTTP API for the Electron UI.

REST endpoints + a Server-Sent-Events stream (/api/stream) that pushes live
log lines, device-status changes, and synced transactions to the UI.

Run:  ./venv/bin/python server.py     (listens on http://127.0.0.1:8000)
"""
import json
import time

from flask import Flask, jsonify, request, Response

import engine

app = Flask(__name__)
eng = engine.Engine()


@app.after_request
def cors(resp):
    # the Electron renderer loads from file://; allow it to call this API
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return resp


@app.get("/api/health")
def health():
    return jsonify(status="ok")


@app.get("/api/devices")
def devices():
    return jsonify(eng.devices())


@app.post("/api/devices/<path:serial>/start")
def start(serial):
    eng.start(serial)
    return jsonify(ok=True)


@app.post("/api/devices/<path:serial>/stop")
def stop(serial):
    eng.stop(serial)
    return jsonify(ok=True)


@app.post("/api/devices/<path:serial>/creds")
def creds(serial):
    b = request.get_json(force=True, silent=True) or {}
    eng.save_device_creds(serial, b.get("username"), b.get("password"))
    if "last_ref" in b:
        eng.set_last_ref(serial, b.get("last_ref") or "")
    return jsonify(ok=True)


@app.get("/api/settings")
def get_settings():
    return jsonify(eng.get_settings())


@app.post("/api/settings")
def post_settings():
    b = request.get_json(force=True, silent=True) or {}
    eng.save_gateway(b.get("client_id", ""), b.get("secret_key", ""))
    # like the Python app's "Save & Setup": register the webhook via /bcel/setup
    setup = eng.setup_webhook(b.get("webhook"))
    return jsonify(ok=True, setup=setup)


@app.post("/api/devices/<path:serial>/mirror")
def mirror(serial):
    eng.mirror(serial)
    return jsonify(ok=True)


@app.post("/api/devices/<path:serial>/disconnect")
def disconnect(serial):
    return jsonify(eng.disconnect(serial))


@app.get("/api/transactions")
def transactions():
    return jsonify(eng.transactions_list())


@app.post("/api/transactions/clear")
def transactions_clear():
    eng.clear_transactions()
    return jsonify(ok=True)


@app.get("/api/sync/status")
def sync_status():
    return jsonify(eng.sync_status())


@app.post("/api/sync/start")
def sync_start():
    b = request.get_json(force=True, silent=True) or {}
    return jsonify(eng.start_sync(b.get("token")))


@app.post("/api/sync/stop")
def sync_stop():
    return jsonify(eng.stop_sync())


@app.post("/api/pair/qr")
def pair_qr():
    return jsonify(eng.start_qr_pair())


@app.get("/api/guide")
def guide():
    import os
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "USER_GUIDE.md")
    try:
        with open(path, encoding="utf-8") as f:
            return jsonify(content=f.read())
    except Exception:
        return jsonify(content="User guide not found.")


@app.get("/api/stream")
def stream():
    def gen():
        q = eng.subscribe()
        try:
            yield "data: " + json.dumps({"kind": "hello"}) + "\n\n"
            while True:
                try:
                    evt = q.get(timeout=20)
                    yield "data: " + json.dumps(evt) + "\n\n"
                except Exception:
                    yield ": keep-alive\n\n"   # comment frame to hold the connection
        finally:
            eng.unsubscribe(q)
    return Response(gen(), mimetype="text/event-stream")


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8000, threaded=True)
