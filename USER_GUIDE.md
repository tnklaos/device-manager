# Device Manager — User Guide

Device Manager connects your Android phones and automatically watches each one
for **incoming payments**, sending them to the payment system for you.

Set up once with the steps below. After that, daily use is just one click.

---

## Step 1 — Open the app

- **Windows:** run the installer, then open **Device Manager** from the Start menu.
  If you see a blue warning, click **More info → Run anyway**.
- **Mac:** open **Device Manager**. The first time, **right-click → Open**.

The window has a menu on the left: **Devices · Transactions · Settings · Guide**.
The dot at the bottom-left turns **green** when the app is connected and ready.

---

## Step 2 — Prepare each phone (one time per phone)

On the phone:

1. Open **Settings → About phone**.
2. Tap **Build number** 7 times (until it says *You are now a developer*).
3. Go to **Settings → Developer options**.
4. Turn on **USB debugging**.
5. (For wireless) also turn on **Wireless debugging**.

---

## Step 3 — Connect the phone

Open the **Devices** page. Connect using whichever is easier:

- **By cable:** plug the phone into the computer and tap **Allow** on the phone.
  It appears in the list automatically.
- **By QR (wireless):** click **📷 Pair device** at the top, then on the phone go to
  **Wireless debugging → Pair device with QR code** and scan the code shown.

Each connected phone appears as a **card**. Add as many phones as you like.

---

## Step 4 — Enter the phone's password

On the phone's card, click the **⚙ (gear)** button and fill in:

- **Password** — the phone's BCEL login password.
- *(Leave **Username** and **Last reference** empty.)*

Click **Save**. The card now shows **● credentials set**.

---

## Step 5 — Set up the payment connection (once)

Open the **Settings** page.

**Payment Gateway**
- Enter your **Client ID** and **Secret Key** *(given to you by your administrator)*.

**Sync** (turns on the public connection so the payment system can be reached)
- Enter your **Sync Token**, then click **Start Sync**. A green dot and a public
  web address appear.

Now click **Save** under Payment Gateway — it saves your keys **and registers the
connection** with the payment system. You should see *"Saved ✓ — webhook registered"*.

> Do **Start Sync first**, then **Save** — Save needs the public address that Sync
> provides.

---

## Step 6 — Start watching for payments

On each phone's card, click **Start**.

- The status dot turns **green** = watching.
- To stop, click **Stop** (dot turns **gray**).

When a payment comes in, the app picks it up and sends it automatically.

---

## The Transactions tab

Click **Transactions** to see every payment that's been sent through, live:

- Columns: **Time, Device, Kind, Type, From (source account), To, Amount, Details, Reference**.
- **Search box** — type any account, name, reference, or amount to filter.
- **Clear log** — empties the list.

---

## Other buttons on a phone card

| Button | What it does |
|---|---|
| **Start / Stop** | Begin / stop watching that phone for payments |
| **⚙** | Enter the phone's password and settings |
| **Mirror** | Show the phone's screen on your computer |
| **Disconnect** | Remove that phone from the list |
| **📷 Pair device** *(top)* | Connect a new phone by scanning a QR code |

---

## Everyday use

1. Open the app.
2. Make sure your phones show in the **Devices** list.
3. Click **Start** on each phone (green dot = working).
4. Leave it running. Watch **Transactions** for incoming payments.

Keep each phone **unlocked**, **connected to the internet**, and **plugged in to
charge** for long sessions.

---

## If something's not working

| Problem | What to do |
|---|---|
| Phone not in the list | Check the cable, make sure **USB debugging** is on, and tap **Allow** on the phone. |
| Phone shows then disappears | The wireless connection dropped — reconnect (Step 3). The app stops watching it until it's back. |
| "Start Sync first" when saving | Click **Start Sync** in Settings first, then **Save** again. |
| Payments not coming through | Make sure the phone has internet, you clicked **Start**, and your **Client ID / Secret Key** are saved. |
| iPhone won't connect | Only **Android** phones are supported. iPhones can't be used. |
| Asked to log in again | Click **⚙** and confirm the **Password** is correct, then **Start** again. |

---

**Keep your keys private.** The Client ID, Secret Key, Sync Token, and phone
passwords are stored on this computer — don't share them.
