# AGENTS.md — Device Manager (BCEL One → Payment Gateway)

Engineering guide for an AI/developer continuing work on this project. Read this
fully before changing the monitoring pipeline or the gateway payload — several
parts are money-critical and have non-obvious invariants.

---

## 1. What this app does

A desktop app (Electron UI + Python engine) that manages many Android phones
running the **BCEL One** Lao banking app, watches each phone for **incoming bank
transactions**, and forwards them — signed — to the **CSL Payment Gateway**
(`https://paymentgateway.108pay.co`).

Pipeline per device, every ~60s:

```
open BCEL → Messages tab → refresh → read the list →
for each NEW incoming row: open its detail, verify it, extract fields →
POST /bcel/transactions (signed) → advance the per-device watermark
```

> **Android only.** iOS can't be driven (no ADB). The bank app is a hybrid
> WebView; resource-ids come from its HTML element ids.

---

## 2. Architecture

```
┌─────────────────────────┐      HTTP + SSE (127.0.0.1:8000)     ┌──────────────────────┐
│ Electron renderer        │ ───────────────────────────────────▶ │ Flask backend         │
│ electron/renderer/*.js   │ ◀─────────────────────────────────── │ server.py             │
│ (UI only, no logic)      │                                      │  └─ engine.Engine()   │
└─────────────────────────┘                                      └──────────┬───────────┘
        ▲ main.js spawns the backend                                         │
        │                                                       per-device monitor threads
                                                                             │
                                                          bcel.py (uiautomator2 + adb)
                                                                             │
                                                          csl_client.py (signed gateway calls)
```

- **electron/main.js** — spawns the Python backend (packaged: `process.resourcesPath/backend/backend(.exe)`; dev: `venv/bin/python server.py`), waits for `/api/health`, opens the BrowserWindow.
- **server.py** — thin Flask REST + SSE layer over a single `engine.Engine()` instance. CORS `*` (renderer loads from `file://`). **No business logic here.**
- **engine.py** — owns per-device monitor threads, settings, the watermark, dedup, the gateway send, ngrok/Sync, QR pairing. **This is the brain.**
- **bcel.py** — all on-device automation (uiautomator2 + adb). Stateless w.r.t. devices (every function takes the `d` device handle).
- **csl_client.py** — gateway request signing (`gen_hash`) and the two POSTs.

### Active vs legacy code
The Electron app uses **only** `bcel.poll_messages`. These are **legacy / not
reached by the app** (used by `automate.py` CLI / old `api.py`): `create_qr`,
`input_fields`, `get_messages`, `_message_row_centers`, `read_notifications`,
`scrape_messages.py`, `run_batch.py`, `device_manager.py`, `api.py`. Don't assume
changes there affect the app.

---

## 3. The monitoring pipeline (bcel.poll_messages) — READ THIS

`poll_messages(serial, last_ref, password, username, fresh, log)` →
`{"first_run": bool, "last_ref": str, "new": [recs]}`.

Steps:
1. `connect(serial, password, username, fresh)` — `u2.connect` + `app_start`
   (`stop=fresh`). Dismisses a "Session expired"/network popup, then re-logs-in
   if the login screen is showing. `fresh=True` forces a full app restart.
2. `open_messages_tab` / `refresh_messages` — tap ↻ then **wait for the list to
   settle** (`_wait_list_settled`: rows present + top row stable across two
   reads), then scroll to the top so the newest message is first.
3. `_list_rows(d)` — one `dump_hierarchy()` snapshot, parsed with ElementTree.
   Returns rows top→bottom with `{key, sig, center, kind, incoming}`.
4. For each row not yet `seen` (dedup by **stable `key`**, not full text):
   - skip outgoing rows (`incoming` is detected from the row title "ໄດ້ຮັບ" /
     positive amount).
   - `read(row)` — re-locate the row by `key`, click its **absolute-pixel
     center**, **wait for the detail to fully render**, extract, **verify it
     matches the clicked row** (`detail_matches_row`: amount + HH:MM:SS must
     agree, no conflict), retry up to 3×, else skip this cycle.
   - watermark/baseline logic (below).

### Watermark / baseline (DO NOT break)
- **First run** (`last_ref is None`): record only the **newest incoming ref** as
  a baseline and STOP. Send nothing (don't flood history).
- **Later runs**: collect new incoming rows top→down until the ref `== last_ref`
  (stop, exclude it). `new_top` = newest verified ref → becomes the new watermark.
- Returned `last_ref` must only ever be a **verified, complete** ref.

### Dedup keys (two layers)
- **`row["key"]` = `kind|HH:MM:SS|amount`** (regex-extracted) — stable even when a
  row is clipped at the scroll edge, so a row isn't re-opened after scrolling.
- **`done_refs`** in-poll set — a ref can't be collected twice in one poll.

---

## 4. Critical invariants (money-critical — break these and users lose money)

1. **The watermark must only advance when the send actually succeeds.** In
   `engine._loop`, `set_last_ref` is gated on `advance = self._send(...)`. A
   transient gateway failure (timeout/network/5xx) returns `False` → watermark
   held → retried next cycle. A 4xx rejection or success returns `True`. Never
   advance on a transient failure or transactions are lost forever.

2. **Global dedup of sent references.** `engine._sent_refs` (OrderedDict, bounded
   10k in memory, 3k persisted to `settings["sent_refs"]`). `_send` drops any txn
   whose `_dedup_key` was already sent — across cycles, fresh restarts, multiple
   devices on the same account, and full app restarts. Real bank refs
   (`bill_no`/`FQR…`/`FAC…`) dedup **globally**; the time-based fallback ref
   (contains `|`) is scoped per `serial+amount`. Refs are marked sent **only after
   a successful POST**.

3. **The detail must match the clicked row.** `detail_matches_row` prevents an
   off-by-one tap (list shifted mid-cycle) from recording the wrong amount/ref.
   `read()` also waits for the detail to fully render before accepting — a
   half-loaded page would otherwise produce a fallback ref instead of the real
   `bill_no`.

4. **The `raw[]` sent to the gateway must match the canonical shape.** The gateway
   (`Utils.getFullAccount` / `handleAutoMateTransaction`) reads FIXED indices:
   `raw[2]="OneBank"`, `raw[4]="ຈາກບັນຊີ"` (or the `|`-pipe QR statement),
   `raw[5]="NAME\naccount"`, `raw[9]=account`. **Newer BCEL builds insert a
   spurious brand element at `raw[2]`** ("OneBank Kid" or a duplicate "OneBank"),
   shifting everything. Fixed in two places (`_extract_message_detail` and
   `engine._send`): *while `raw[3] == "OneBank"`, delete `raw[2]`*. Don't remove
   this guard.

5. **Source account** = where the money came FROM. `engine.source_account(t)`
   prefers `from_account`/`from_name` set by `bcel.row_source(sig)` (parsed from
   the reliable list-row text). Pipe/QR: `parts[2]` (or `parts[5]` for OnePay) +
   `parts[5]` name. Transfer: `ຈາກບັນຊີ: NAME - ACCOUNT`. Falls back to the old
   `raw[]`-index logic only if those aren't present.

---

## 5. Gateway integration (csl_client.py)

- **Auth**: `gen_hash(payload, api_key)` = MD5 (uppercase) of `key=value` pairs
  sorted case-insensitively and joined with `&api-key=<key>` placed **between**
  pairs (a single-key body has no separator). Sent as headers `client-id` +
  `hash-signature`. This mirrors the gateway's `Utils.genHash`.
- **`setup_webhook(api_url, client_id, api_key, webhook)`** → `POST /bcel/setup`.
  Returns `(ok, msg)`. Used to register the public Sync URL and to **verify
  credentials** (a wrong client_id/api_key is rejected).
- **`post_transactions(api_url, client_id, api_key, txns)`** → `POST
  /bcel/transactions`. Returns **`(ok, msg, transient)`**. `transient=True` for
  network/timeout/5xx — the caller must retry, not advance the watermark.

---

## 6. Setting sets (multi-tenant) & device assignment

- A **set** = a named gateway profile: `{name, client_id, api_key, api_url,
  webhook}` under `settings["sets"][<id>]`. `api_url` is optional (defaults to
  `GATEWAY_API_URL`); the UI hides the API-URL field.
- Each device stores `settings["devices"][serial]["set"] = <set_id>`.
- `_send` uses the **device's assigned set's** credentials. No set / blank creds →
  the txns are **held** (watermark not advanced) so they send once configured.
- UI: Settings page is **tabbed** (one tab per set + "＋ Add set" + a global
  "Sync" tab). Each device card has a **set dropdown**. Saving a set also calls
  `/bcel/setup` to verify the credentials.

---

## 7. settings.json schema

Dev: `./settings.json` (gitignored). Packaged: `~/.device-manager/settings.json`.

```jsonc
{
  "sets": { "<id>": { "name": "...", "client_id": "...", "api_key": "...",
                       "api_url": "", "webhook": "" } },
  "devices": { "<serial>": { "username": "", "password": "",
                             "last_ref": "", "set": "<id>" } },
  "ngrok_token": "",
  "webhook": "",            // last detected public Sync URL
  "sent_refs": ["...", ...] // persisted dedup tail (last 3000)
}
```

All settings writes go through `engine._slock` (RLock) — multiple device threads
+ Flask threads write concurrently, so never `save_settings` outside the lock.

---

## 8. Sync (ngrok) — global, optional

`save_token` / `start_sync` / `stop_sync` / `sync_status` in engine.py. One tunnel
for the whole machine, exposing port 8000 publicly so the gateway can reach the
local API. `_detect_tunnel` reads ngrok's local API on `:4040` (and adopts an
already-running tunnel). **Security note:** the tunnel exposes *all* `/api/*`
endpoints publicly — never put secrets (e.g. device passwords) in `/api/devices`.

---

## 9. Resilience (added for real-world internet/server issues)

- **All adb calls have timeouts** (`engine.adb`, `bcel._adb`) and return `""` on
  timeout — a wedged adb server can't hang the monitor thread or the UI.
- **Offline tolerance** (`OFFLINE_TOLERANCE=3`): `_loop` tolerates a few
  consecutive "not reachable" cycles (status `reconnecting`), runs `adb connect
  ip:port` for Wi-Fi devices, and auto-recovers — a brief Wi-Fi drop won't kill a
  monitor. `unauthorized` stops immediately (needs a human to tap Allow).
- **`_loop` `except` never hard-stops** on a single error; the tolerant
  top-of-loop check governs stopping.
- **Slow fetch**: `refresh_messages` waits for the list to settle; `read()` waits
  for the detail to render before extracting.
- **Slow/down gateway**: bounded POST timeout (10s); transient → hold watermark +
  retry; persisted dedup prevents double-send on retry.
- **Fresh restart** every `FRESH_RESTART_CYCLES` (30) poll cycles per device
  (tracked globally in `engine._cycles`, survives stop/start) clears stale
  sessions / frozen WebViews.

---

## 10. Screen-size independence

The monitoring path is **resolution-independent**:
- `_list_rows` uses fractions of `d.info` width/height AND the real bounds of the
  scrollable list container for the viewport (not fixed pixels).
- `read()` clicks the row element's **absolute-pixel center** from its own bounds.
- Navigation/parsing is by **resource-id / text**, not coordinates.

Still pixel-hardcoded but **not used by the app**: `input_fields`/`create_qr`
(QR creation, CLI only). If QR creation is ever wired into the app, make
`input_fields` relative first.

---

## 11. Hard security boundary (respect this)

This project integrates with the **official** gateway API and reads the bank
app's own UI (equivalent to a UI dump). Do **NOT**:
- decompile/reverse-engineer the BCEL APK,
- bypass its anti-automation / anti-tamper controls (e.g. coordinate-injection
  workarounds when WebView accessibility is disabled),
- defeat login/session protections.

BCEL v4.31+ hardened the WebView against automation; the **sustainable path is the
official gateway integration**, which is fully built. If the on-device read path
breaks due to bank hardening, do not try to defeat it — escalate to using an
official bank feed into the gateway.

---

## 12. Build & run

**Dev:**
```bash
python3 -m venv venv && ./venv/bin/pip install -r requirements.txt
./venv/bin/python server.py            # backend on :8000
cd electron && npm install && npm start # UI (spawns its own backend in packaged mode)
```
Restart the backend after editing Python: `lsof -ti :8000 | xargs kill -9; ./venv/bin/python server.py`.

**Package:** PyInstaller `Backend.spec` → `dist/backend`, bundled into Electron via
`extraResources` (`electron/package.json`). Windows `.exe` builds in GitHub Actions
(`.github/workflows/`). See **BUILD.md**. Keep `APP_VERSION` (engine.py) and
`electron/package.json` version in sync; the UI shows `APP_VERSION` (sidebar) from
`/api/health`.

---

## 13. REST + SSE API (server.py)

`GET /api/health` · `GET /api/devices` · `POST /api/devices/<serial>/start|stop|creds|set|mirror|disconnect`
· `GET/POST /api/sets` · `POST /api/sets/<id>/delete|webhook`
· `GET /api/settings` · `GET /api/transactions` · `POST /api/transactions/clear`
· `GET /api/sync/status` · `POST /api/sync/start|stop` · `POST /api/pair/qr`
· `GET /api/guide` · `GET /api/stream` (SSE: `log`, `device`, `transaction`, `pair`).

Device password is **never** returned to the UI (would leak over Sync); the creds
modal shows a "saved" placeholder and only sends the password when the user types
a new one (so editing other fields can't wipe it).

---

## 14. Known limitations / good next tasks

- `uiautomator2` calls inside a poll rely on u2's internal HTTP timeouts; a
  half-hung Wi-Fi device could stall *that one* device thread (contained — daemon
  thread, other devices/UI unaffected). A watchdog/timeout wrapper would fully
  bound it.
- `input_fields`/`create_qr` are pixel-hardcoded (legacy).
- Dead code to remove: `_message_row_centers`.
- A failed-then-recovered gateway can re-POST a whole batch each cycle until it
  succeeds; only successful refs are marked sent (correct, but worth noting).
- The Thai USER_GUIDE translation was requested but never finished.

---

## 15. Glossary

- **TRI** transfer-in, **TRO** transfer-out, **ACC** account credit (QR/LMPS),
  **LMPS QR TRANSFER IN** the QR pipe statement.
- **watermark** = `last_ref`, the newest already-processed reference per device.
- **fresh** = full app stop+start (vs lightweight resume) to clear stale state.
- **set** = a named gateway credential profile a device is assigned to.
