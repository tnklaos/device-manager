# Device Manager

A desktop app to manage multiple Android phones and monitor incoming bank
transactions (BCEL One), forwarding them to the Payment Gateway.

It does three things:

1. **Connect & control** many Android devices (USB / WiFi / QR pairing).
2. **Listen** for incoming transactions on each device and **forward** them to the
   gateway (`/bcel/transactions`, signed).
3. Expose a small **HTTP API** (optionally over a public **Sync** tunnel) for
   remote actions.

> **Android only.** iOS devices can't be controlled (Apple sandbox + no ADB).

---

## 1. Install

### macOS
1. Download **`DeviceManager.app`** (from a release, or `dist/` after a build).
2. First launch: right-click → **Open** (it's unsigned), or run once:
   ```bash
   xattr -dr com.apple.quarantine /path/to/DeviceManager.app
   ```
3. Double-click to run.

### Windows
1. Download **`DeviceManager.exe`** (from the GitHub Actions artifact or a release).
2. Double-click to run. (SmartScreen may warn → **More info → Run anyway**.)

**Nothing else to install.** Python, `adb`, and `ngrok` are bundled inside the app.

> Optional: the **Mirror** button needs **scrcpy** on the system PATH
> (`brew install scrcpy` / [scrcpy releases](https://github.com/Genymobile/scrcpy/releases)).
> Everything else works without it.

---

## 2. Connect a device

On the phone (one-time):
- **Settings → About phone → tap Build number 7×** to unlock Developer options.
- **Developer options → enable USB debugging** (and **Wireless debugging** on Android 11+).

Then in the app's **Devices** page, connect one of three ways:

| Method | How |
|---|---|
| **USB** | Plug in, tap **Allow** on the phone, click **↻** (refresh). |
| **WiFi (IP)** | Type `ip:port` in the field → **Connect**. |
| **WiFi (QR)** | Click **📷 QR** → scan the code with the phone's *Wireless debugging → Pair device with QR code*. |

Connected devices appear as rows. Click **↻** to rescan anytime.

---

## 3. Set per-device credentials

Click **Set** on a device row and fill:

- **Username** — usually left blank (the app remembers the account).
- **Password** — the BCEL login password (used only if the session expires).
- **Last reference (monitor watermark)** — leave blank to auto-set on the first
  poll; or paste a reference to resume from a specific point. **Reset** clears it.

Saved per device (encrypted-at-rest is *not* applied — see Security below). The
gear/**Set** turns green once credentials are stored.

---

## 4. Configure the gateway (Settings page)

Open **Settings** in the sidebar.

### Payment Gateway
- **Client ID** — your gateway client id.
- **Secret Key** — your gateway API key (used to sign requests).
- Click **Save**.

The app signs every gateway call (`client-id` + MD5 `hash-signature`) and posts
transactions to `https://paymentgateway.108pay.co/bcel/transactions`.

### Sync (public tunnel) — optional
Only needed if you want the gateway/others to reach this machine's HTTP API.
- **Sync Token** — your ngrok auth token (from
  [dashboard.ngrok.com](https://dashboard.ngrok.com/get-started/your-authtoken)) → **Save**.
- **Start Sync** → opens a public HTTPS URL (shown as **Public URL**, with **Copy**).
- The sidebar shows **● Sync: on/off**. **Stop** tears it down.

---

## 5. Monitor transactions

Each device row has two run modes:

| Button | What it does | Needs login? |
|---|---|---|
| **Listen** *(recommended)* | Reads incoming transactions from **notifications** every 20s. No app automation. | No |
| **Play** | Opens the app and reads the **Messages** tab every 60s. | Yes |

**How it works** (both modes):
- On the **first run** it records the newest incoming transaction as a *baseline*
  and **sends nothing** (so it won't flood history).
- On later runs it collects only **new** incoming transactions (transfers-in and
  QR/LMPS payments-in), **stops at the last-seen reference**, and **POSTs them to
  the gateway**. Outgoing transactions are ignored.
- If nothing new arrived, it sends nothing.

Controls:
- **Stop** — stop monitoring that device.
- **Run All** — start every connected device at once.
- **Disconnect All** — drop all WiFi devices.
- Disconnected devices stop monitoring automatically.

Watch progress in the **Output** panel at the bottom and the `running / total`
counter at the top.

> ⚠️ **Notifications can be incomplete** — if the bank doesn't post a notification
> for a transaction, **Listen** can't see it. The reliable long-term source is an
> official bank feed into the gateway.

---

## 6. HTTP API (advanced)

When **Sync** is running (or locally on `http://127.0.0.1:8000`):

| Method | Path | Body |
|---|---|---|
| GET | `/health` | — |
| GET | `/devices` | — |
| POST | `/qr` | `{serial, amount, description, password?}` → returns the QR string |
| POST | `/messages` | `{serial, password?}` → returns scraped transactions |

Same-device requests are serialized; different devices run in parallel.

---

## 7. Security notes

- **Credentials** (gateway Secret Key, ngrok token, device passwords) are stored
  in `settings.json` next to the app, in **plain text**. Keep the machine secure.
- Exposing the API via **Sync** means anyone with the URL can reach it — the
  gateway endpoints still require the signed headers, but treat the URL as
  sensitive.
- `settings.json` and `devices.txt` are **gitignored** — never commit them.

---

## 8. Build from source

See **[BUILD.md](BUILD.md)**. Quick version:

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt pyinstaller
python make_icon.py
./venv/bin/pyinstaller --noconfirm --clean DeviceManager.spec   # macOS .app
```

Windows builds run automatically in **GitHub Actions** (`.github/workflows/build-windows.yml`)
on every push to `main`, on `v*` tags, or via the **Run workflow** button — the
`.exe` is in the run's **Artifacts** (and attached to a Release for tags).

---

## 9. Troubleshooting

| Problem | Fix |
|---|---|
| Device not in the list | Enable USB debugging, tap **Allow**, click **↻**. iPhones never appear (Android only). |
| Console windows flash (Windows) | Fixed in current builds (`CREATE_NO_WINDOW`). Rebuild the `.exe`. |
| "Cannot connect to sync service" | Gateway URL/credentials wrong, or the gateway is down — check **Settings**. |
| Sync fails: "Invalid token" | Get a fresh token from the ngrok dashboard → **Save** → **Start Sync**. |
| Monitor keeps scrolling / re-reading | Make sure you're on a current build (refresh-to-top + watermark fixes). |
| Login can't get past password | Newer BCEL versions harden the login against automation — use **Listen** (no login). |
| App "broke" the bank login | Remove the automation agent: `adb uninstall com.github.uiautomator` (+ `.test`), turn off Developer options, reboot. |
