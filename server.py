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
    return jsonify(status="ok", version=engine.APP_VERSION)


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


@app.post("/api/settings/log-retention")
def set_log_retention():
    b = request.get_json(force=True, silent=True) or {}
    return jsonify(eng.set_log_retention(b.get("value")))


# ---------- setting sets (named gateway profiles) ----------
@app.get("/api/sets")
def get_sets():
    return jsonify(eng.sets())


@app.post("/api/sets")
def save_set():
    b = request.get_json(force=True, silent=True) or {}
    set_id = eng.save_set(b.get("id"), b.get("name", ""), b.get("client_id", ""),
                          b.get("secret_key", ""), b.get("api_url", ""))
    return jsonify(ok=True, id=set_id)


@app.post("/api/sets/<set_id>/delete")
def delete_set(set_id):
    return jsonify(eng.delete_set(set_id))


@app.post("/api/sets/<set_id>/webhook")
def set_webhook(set_id):
    return jsonify(eng.setup_set_webhook(set_id))


@app.post("/api/devices/<path:serial>/set")
def assign_set(serial):
    b = request.get_json(force=True, silent=True) or {}
    return jsonify(eng.assign_device_set(serial, b.get("set_id") or ""))


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
