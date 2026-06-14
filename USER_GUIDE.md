# Device Manager — User Guide

Device Manager connects your Android phones and automatically watches each one
for **incoming payments**, sending them to the payment system for you.

Follow these steps once to set up. After that, daily use is just one click.

---

## Step 1 — Open the app

- **Windows:** double-click **DeviceManager.exe**.
  If you see a blue warning, click **More info → Run anyway**.
- **Mac:** double-click **DeviceManager**.
  The first time, **right-click the app → Open** (then click *Open* again).

Nothing else needs to be installed.

---

## Step 2 — Prepare each phone (one time per phone)

On the phone:

1. Open **Settings → About phone**.
2. Tap **Build number** 7 times (until it says *You are now a developer*).
3. Go back to **Settings → Developer options**.
4. Turn on **USB debugging**.
5. (For wireless) also turn on **Wireless debugging**.

---

## Step 3 — Connect the phone to the app

Open the **Devices** page (left menu). Connect using whichever is easier:

- **By cable:** plug the phone into the computer. On the phone, tap **Allow**
  when it asks about USB debugging. Then click the **↻** (refresh) button.
- **By WiFi:** type the phone's address (shown in *Wireless debugging*) in the
  box and click **Connect** — or click **📷 QR** and scan the code with the
  phone's *Wireless debugging → Pair device with QR code*.

Your phone now appears as a row in the list. (You can add as many phones as you
want — repeat for each.)

---

## Step 4 — Enter the phone's login password

1. On the phone's row, click **Set**.
2. Type the phone's **BCEL password** in the **Password** box.
   (Leave **Username** and **Last reference** empty.)
3. Click **Save**. The **Set** button turns green.

---

## Step 5 — Enter your account keys (once)

1. Open the **Settings** page (left menu).
2. Under **Payment Gateway**, enter:
   - **Client ID**
   - **Secret Key**

   *(These are given to you by your administrator.)*
3. Click **Save**.

---

## Step 6 — Start watching for payments

On each phone's row, click **Listen**.

- The row turns active and shows **listening**.
- To start every phone at once, click **Run All** at the top.

That's it. When a payment comes in, the app picks it up and sends it
automatically. You can see what's happening in the **Output** box at the bottom.

To stop a phone, click **Stop**.

---

## Everyday use

1. Open the app.
2. Make sure your phones show in the list (click **↻** if not).
3. Click **Run All** (or **Listen** on each phone).
4. Leave it running.

Keep each phone:
- **Screen on / unlocked** while it's working,
- **Connected to the internet**, and
- **Plugged in to charge** for long sessions.

---

## If something's not working

| Problem | What to do |
|---|---|
| Phone not in the list | Check the cable, make sure **USB debugging** is on, tap **Allow** on the phone, then click **↻**. |
| Phone shows then disappears | The WiFi connection dropped — reconnect (Step 3). It stops watching that phone automatically until you reconnect. |
| Payments not coming through | Make sure the phone has internet, you clicked **Listen**, and your **Client ID / Secret Key** are saved in Settings. |
| iPhone won't connect | Only **Android** phones are supported. iPhones can't be used. |
| Asked to log in / enter password again | Click **Set** and confirm the **Password** is correct, then **Listen** again. |

---

## Buttons at a glance

| Button | What it does |
|---|---|
| **Listen** | Start watching that phone for incoming payments (recommended) |
| **Stop** | Stop watching that phone |
| **Set** | Enter the phone's password / settings |
| **Mirror** | Show the phone's screen on your computer |
| **Disconnect** | Remove that phone |
| **Run All** | Start watching all phones |
| **Disconnect All** | Remove all phones |
| **↻** | Refresh the phone list |
| **📷 QR** | Connect a phone by scanning a QR code |

---

**Keep your keys private.** The Client ID, Secret Key, and phone passwords are
stored on this computer — don't share them.
