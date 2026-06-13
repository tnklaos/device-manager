"""
Reusable BCEL One automation actions, shared by the CLI (automate.py),
the batch runner, and the HTTP API (api.py).

Public functions:
    create_qr(serial, amount, description, password="", submit=True) -> dict
    get_messages(serial, max_scrolls=8) -> list[dict]

Both connect over ADB, log in if the session expired, then drive the UI.
NOTE: operations on the SAME device must not run concurrently (they share one
screen). api.py enforces a per-device lock; if you call these directly, do the
same.
"""
import os
import re
import time
import subprocess
import uiautomator2 as u2

PKG = "com.bcel.bcelone"
HERE = os.path.dirname(os.path.abspath(__file__))


# hide the per-child console window on Windows (0 on macOS/Linux)
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


# ----------------- notification reading (pure adb, no agent) -----------------
def _adb(serial, *args):
    return subprocess.run(["adb", "-s", serial, *args],
                          capture_output=True, text=True,
                          creationflags=NO_WINDOW).stdout


def read_notifications(serial, pkg=PKG):
    """Read active notifications for `pkg` via `dumpsys notification --noredact`.
    Pure adb — does NOT install the uiautomator agent or touch the app UI.
    Returns a list of dicts: {key, pkg, title, text, bigText, when}.
    """
    out = _adb(serial, "shell", "dumpsys", "notification", "--noredact")
    records, cur = [], None

    def flush():
        if cur and cur["pkg"] == pkg and (cur["title"] or cur["text"] or cur["bigText"]):
            records.append(cur)

    for raw in out.splitlines():
        s = raw.strip()
        if s.startswith("NotificationRecord("):
            flush()
            pm = re.search(r":\s*([\w.]+)\s*/", s) or re.search(r"pkg=([\w.]+)", s)
            cur = {"pkg": pm.group(1) if pm else "", "key": "", "title": "",
                   "text": "", "bigText": "", "when": ""}
            continue
        if cur is None:
            continue
        if not cur["key"]:
            km = re.search(r"\bkey=(\S+)", s)
            if km:
                cur["key"] = km.group(1)
        for field, k in (("android.title", "title"), ("android.text", "text"),
                         ("android.bigText", "bigText")):
            fm = re.search(re.escape(field) + r"=String \((.*)\)\s*$", s)
            if fm:
                cur[k] = fm.group(1)
        wm = re.search(r"mWhen=(\d+)", s)
        if wm:
            cur["when"] = wm.group(1)
    flush()
    return records


def notification_to_txn(n, serial):
    """Turn a BCEL notification into a transaction dict (best-effort)."""
    title = n.get("title", "")
    body = n.get("bigText") or n.get("text") or ""
    incoming = any(w in (title + body) for w in ("ໄດ້ຮັບເງິນໂອນ", "ໄດ້ຮັບ", "received", "Received"))
    am = re.search(r"([\d,]+(?:\.\d+)?)\s*(LAK|USD)", body)
    when = ""
    if n.get("when", "").isdigit():
        when = time.strftime("%d/%m/%Y %H:%M:%S", time.localtime(int(n["when"]) / 1000))
    return {
        "type": title,
        "kind": "TRI" if incoming else "",
        "raw": [body],
        "amount_in": (am.group(0) if (am and incoming) else ""),
        "time": when,
        "ref": n.get("key", "") or f"{title}|{n.get('when','')}",
        "serial": serial,
        "source": "notification",
    }


# ----------------- low-level helpers -----------------
def tag_for(serial):
    return serial.replace(":", "_").replace(".", "-")


def clear_and_type(d, value, n=16):
    # one ADB round-trip: MOVE_END (123) then n backspaces (67), instead of
    # firing n+1 separate keyevents over WiFi.
    d.shell("input keyevent 123 " + ("67 " * n))
    d.send_keys(str(value))


def fill(d, xy, value, passes=1):
    """Focus a custom input box, clear, then type. Pass passes=2 for the first
    field after a page load (its input can be swallowed while the IME attaches)."""
    for _ in range(passes):
        d.click(*xy)
        time.sleep(0.35)
        clear_and_type(d, value)
        time.sleep(0.2)


def input_fields(d):
    """(amount_xy, desc_xy) center coords of the two Create-QR input boxes."""
    found = []
    for el in d.xpath('//*').all():
        m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", el.attrib.get("bounds", ""))
        if not m:
            continue
        x1, y1, x2, y2 = map(int, m.groups())
        if (el.attrib.get("clickable") == "true" and el.attrib.get("focusable") == "true"
                and (x2 - x1) > 800 and 1700 < y1 and y2 < 2150 and (y2 - y1) < 200):
            found.append((y1, (x1 + x2) // 2, (y1 + y2) // 2))
    found.sort()
    if len(found) < 2:
        raise RuntimeError(f"expected 2 input fields, found {len(found)}")
    return (found[0][1], found[0][2]), (found[1][1], found[1][2])


def tap(d, sel, timeout=20):
    if not d(text=sel).wait(timeout=timeout):
        raise RuntimeError(f"element not found: {sel!r}")
    d(text=sel).click()


def tap_clickable(d, sel, timeout=20):
    """Click the clickable element with this text (title vs button share text)."""
    el = d(text=sel, clickable=True)
    if not el.wait(timeout=timeout):
        raise RuntimeError(f"clickable element not found: {sel!r}")
    el.click()


def bottom_most_xy(d, sel):
    cands = []
    for el in d.xpath('//*').all():
        if (el.text or "").strip() == sel:
            m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", el.attrib.get("bounds", ""))
            if m:
                x1, y1, x2, y2 = map(int, m.groups())
                cands.append((y1, (x1 + x2) // 2, (y1 + y2) // 2))
    if not cands:
        raise RuntimeError(f"button not found: {sel!r}")
    cands.sort()
    return cands[-1][1], cands[-1][2]


def decode_qr(path):
    import cv2
    img = cv2.imread(path)
    data, _, _ = cv2.QRCodeDetector().detectAndDecode(img)
    return data


# ----------------- login -----------------
def at_login(d):
    if d.app_current().get("activity", "").endswith("BcelOneLogin"):
        return True
    return d(resourceId="login").exists


def do_login(d, pwd, username="", log=print):
    if not pwd:
        raise RuntimeError("login required but no password provided")
    log("login screen detected — signing in")
    d.set_input_ime(True)
    time.sleep(0.5)
    # Username: only entered if the login screen actually shows an account field.
    # Normally the app remembers the account, so this is skipped.
    if username:
        for rid in ("username", "account", "phone", "user", "userId"):
            el = d(resourceId=rid)
            if el.exists:
                el.click()
                time.sleep(0.4)
                clear_and_type(d, username)
                log("entered username")
                break
    # Password: entered every time we hit the login screen.
    d(resourceId="password").click()
    time.sleep(0.6)
    clear_and_type(d, pwd)
    time.sleep(0.5)
    d.set_input_ime(False)
    d(resourceId="login").click()
    deadline = time.time() + 45
    while time.time() < deadline:
        act = d.app_current().get("activity", "")
        if act.endswith("MainActivity") or d(text="My QR").exists:
            time.sleep(2)
            log("logged in")
            return
        time.sleep(1)
    raise RuntimeError("login did not reach the dashboard (wrong password, OTP, or popup?)")


def connect(serial, password="", username="", log=print, fresh=False):
    """Connect and resume the app (don't restart by default — that keeps the
    session alive so repeated calls skip the slow login). Logs in only if the
    session actually expired. Pass fresh=True to force a clean restart."""
    d = u2.connect(serial)
    d.app_start(PKG, stop=fresh)
    time.sleep(1.0)
    if at_login(d):
        do_login(d, password, username, log=log)
    return d


def is_home(d):
    """Dashboard = MainActivity. ('My QR' text is unreliable — it stays in the
    WebView DOM on sub-pages too, so key on the activity instead.)"""
    return d.app_current().get("activity", "").endswith("MainActivity")


def go_home(d, tries=5, log=lambda *_: None):
    """Press Back until the dashboard (MainActivity) is showing. If a Back press
    exits the app, relaunch once and stop — never lands on the launcher."""
    for _ in range(tries):
        cur = d.app_current()
        if cur.get("package") != PKG:        # we left the app
            d.app_start(PKG, stop=False)
            time.sleep(1.5)
            return is_home(d)
        if cur.get("activity", "").endswith("MainActivity"):
            # MainActivity has bottom tabs; Back can land on the wrong one.
            # 'My QR' lives on the ບັດ (Card) tab — select it if needed.
            if not d(text="My QR").exists and d(text="ບັດ").exists:
                d(text="ບັດ").click()
                time.sleep(1)
            return True
        log(f"go_home: back from {cur.get('activity')}")
        d.press("back")
        time.sleep(0.4)
    return is_home(d)


# ----------------- actions -----------------
def create_qr(serial, amount, description, password="", username="", submit=True,
              go_home_after=True, log=print):
    """Create a 'QR with amount' and return a result dict:
        {serial, amount, description, qr_string, screenshot, home}
    If go_home_after is False, skips the final return-to-dashboard step (the
    API runs that in the background so the caller isn't blocked on it)."""
    d = connect(serial, password, username, log=log)
    go_home(d)            # normalize to the dashboard (cheap if already there)

    tap(d, "My QR")
    tap(d, "LAK")
    tap_clickable(d, "ສ້າງ QR ມີຈຳນວນ")

    amount_xy = desc_xy = None
    for _ in range(10):
        try:
            amount_xy, desc_xy = input_fields(d)
            break
        except RuntimeError:
            time.sleep(1)
    if not amount_xy:
        raise RuntimeError("Create-QR input fields never appeared")

    d.set_input_ime(True)
    time.sleep(0.4)                       # let the IME attach before first input
    fill(d, amount_xy, amount, passes=2)  # first field: double-pass (swallow guard)
    log(f"entered amount: {amount}")
    fill(d, desc_xy, description)         # IME warm now: single pass is enough
    log(f"entered description: {description}")
    d.set_input_ime(False)

    tag = tag_for(serial)
    result = {"serial": serial, "amount": str(amount), "description": description,
              "qr_string": "", "screenshot": "", "home": False}

    if submit:
        sx, sy = bottom_most_xy(d, "ສ້າງ QR ມີຈຳນວນ")
        d.click(sx, sy)
        log("submitted — confirming")
        if d(text="ຖືກແລ້ວ").wait(timeout=10):
            d(text="ຖືກແລ້ວ").click()
        d(text="QR ມີຈຳນວນ").wait(timeout=20)
        shot = os.path.join(HERE, f"qr_{tag}.png")
        d.screenshot(shot)
        result["screenshot"] = shot
        result["qr_string"] = decode_qr(shot) or ""
        log(f"QR string: {result['qr_string'] or '(decode failed)'}")
        # return to the dashboard so the app is ready for the next action
        if go_home_after:
            result["home"] = go_home(d)
            log("back on home" if result["home"] else "warning: could not reach home")
        else:
            result["home"] = None   # caller will return home in the background
    else:
        shot = os.path.join(HERE, f"qr_{tag}.png")
        d.screenshot(shot)
        result["screenshot"] = shot

    return result


_MSG_LABELS = {"ຫາບັນຊີ": "to_account", "ລາຍລະອຽດ": "details", "ເລກໃບບິນ": "bill_no",
               "ເງິນອອກ": "amount_out", "ເງິນເຂົ້າ": "amount_in", "ຈຳນວນເງິນ": "amount"}


def _extract_message_detail(d):
    """Read the open message-detail screen into a dict, incl. a unique 'ref'."""
    rec = {"type": "", "account": "", "time": "", "kind": "", "raw": []}
    pending = None
    for el in d.xpath('//*').all():
        t = (el.text or "").strip()
        rid = (el.attrib.get("resource-id") or "").split("/")[-1]
        if not t or rid in ("tab_text", "clock", "battery_percentage_view"):
            continue
        if rid == "titletext":
            rec["type"] = t
        elif rid == "source":
            rec["account"] = t
        elif rid == "time":
            rec["time"] = t
        elif rid == "subtitlehead":
            rec["kind"] = t
        elif rid == "titleprev":
            continue
        else:
            rec["raw"].append(t)
            if t in _MSG_LABELS:
                pending = _MSG_LABELS[t]
            elif pending:
                rec[pending] = t
                pending = None
    # reference: prefer the bill number; fall back to timestamp+type (unique)
    rec["ref"] = rec.get("bill_no") or f"{rec.get('time')}|{rec.get('type')}"
    return rec


def _message_row_centers(d):
    """Normalized y-centers of the message rows, top to bottom."""
    h = d.info.get("displayHeight", 2408)
    rows = []
    for el in d.xpath('//*[@clickable="true"]').all():
        m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", el.attrib.get("bounds", ""))
        if not m:
            continue
        x1, y1, x2, y2 = map(int, m.groups())
        if x1 == 0 and x2 >= 1000 and y2 < 2100 and (y2 - y1) > 150:
            rows.append((y1, (y1 + y2) / 2 / h))
    rows.sort()
    return [cy for _, cy in rows]


def _parse_bounds(bounds):
    match = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds or "")
    if not match:
        return None
    return tuple(map(int, match.groups()))


def open_messages_tab(d):
    # A message *detail* shares the .MainActivity activity and keeps 'My QR' in
    # the DOM, so go_home/back/tab-taps can't dismiss it — only its Close button
    # does. Navigate to the Messages tab, then close any lingering detail until
    # the list (full-width rows) is visible.
    go_home(d)
    if d(text="ບັດ").exists:
        d(text="ບັດ").click()
        time.sleep(0.8)
    if not d(text="ຂໍ້ຄວາມ").wait(timeout=15):
        raise RuntimeError("Messages tab not found")
    d(text="ຂໍ້ຄວາມ").click()
    time.sleep(1.2)
    for _ in range(3):
        if _list_rows(d):                 # list is showing
            return
        if d(text="Close").exists:        # a detail is open -> close it
            d(text="Close").click()
            time.sleep(1.2)
        else:
            break


def refresh_messages(d):
    """Tap the message refresh icon without relying on one fixed offset."""
    display_width = d.info.get("displayWidth", 1080)
    display_height = d.info.get("displayHeight", 2408)

    title_candidates = []
    for el in d.xpath('//*').all():
        if (el.text or "").strip() == "ຂໍ້ຄວາມ":
            bounds = _parse_bounds(el.attrib.get("bounds", ""))
            if bounds:
                title_candidates.append(bounds)
    title_bounds = next(
        (bounds for bounds in sorted(title_candidates, key=lambda item: item[1])
         if bounds[1] < display_height * 0.4),
        title_candidates[0] if title_candidates else None)

    title_center_y = ((title_bounds[1] + title_bounds[3]) // 2
                      if title_bounds else int(display_height * 0.125))
    y_tolerance = (max(80, (title_bounds[3] - title_bounds[1]) * 1.3)
                   if title_bounds else display_height * 0.06)
    candidates = []
    for el in d.xpath('//*[@clickable="true"]').all():
        bounds = _parse_bounds(el.attrib.get("bounds", ""))
        if not bounds:
            continue
        left, top, right, bottom = bounds
        center_x, center_y = (left + right) // 2, (top + bottom) // 2
        if center_x < display_width * 0.75:
            continue
        if abs(center_y - title_center_y) > y_tolerance:
            continue
        if bottom - top > display_height * 0.12:
            continue
        candidates.append((center_x, center_y))

    if candidates:
        center_x, center_y = sorted(candidates, reverse=True)[0]
        d.click(center_x, center_y)
    else:
        d.click(0.93, title_center_y / display_height)
    time.sleep(2.0)
    # Scroll back to the very top so every cycle reads from the NEWEST message.
    # Without this, a list left scrolled-down from the previous cycle makes the
    # poll read older transactions (ref != last_ref) as if they were new and keep
    # scrolling. Swiping "down" reveals content above (newest is first).
    for _ in range(3):
        d.swipe_ext("down", scale=0.8)
        time.sleep(0.3)
    time.sleep(0.4)


def _list_rows(d):
    """Message rows currently on screen, top->bottom. Each row is classified as
    incoming/outgoing from its amount sign in the list (outgoing = red, '-')."""
    h = d.info.get("displayHeight", 2408)
    conts = []
    for el in d.xpath('//*[@clickable="true"]').all():
        m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", el.attrib.get("bounds", ""))
        if not m:
            continue
        x1, y1, x2, y2 = map(int, m.groups())
        # y1 > 240 skips the header bars (where the ☰ menu / ↻ refresh live), so
        # a row tap can never land on the menu icon. y2 < 2100 skips the tab bar.
        if x1 == 0 and x2 >= 1000 and 240 < y1 and y2 < 2100 and (y2 - y1) > 150:
            conts.append((y1, y2))
    conts.sort()
    texts = []
    for el in d.xpath('//*').all():
        t = (el.text or "").strip()
        m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", el.attrib.get("bounds", ""))
        if t and m:
            x1, y1, x2, y2 = map(int, m.groups())
            texts.append(((y1 + y2) // 2, t))
    rows = []
    for (y1, y2) in conts:
        rt = [t for (yc, t) in texts if y1 <= yc <= y2]
        sig = " | ".join(rt)
        # the row's leading badge text is the kind code (TRI/TRO/ACC/SAL/TOP)
        km = re.search(r"\b(TRI|TRO|ACC|SAL|TOP|TFO)\b", sig)
        kind = km.group(1) if km else ""
        # "incoming" = money received. Detect by the title ("ໄດ້ຮັບ" = received)
        # or a positive amount (outgoing amounts are red and start with '-').
        # This catches BOTH transfers-in (TRI, ໄດ້ຮັບເງິນໂອນ) and QR/LMPS
        # payments-in (ACC, ໄດ້ຮັບເງິນ) — kind alone is not enough since ACC is
        # used for both incoming and outgoing.
        am = re.search(r"(-?\s*[\d,]+(?:\.\d+)?)\s*(LAK|USD)", sig)
        positive = bool(am) and am.group(1).lstrip()[:1] not in ("-", "−")
        incoming = ("ໄດ້ຮັບ" in sig) or positive
        rows.append({"cy": (y1 + y2) / 2 / h, "sig": sig, "kind": kind,
                     "incoming": incoming})
    return rows


def poll_messages(serial, last_ref=None, password="", username="", max_scrolls=6,
                  kinds=None, log=print):
    """Incremental poll for incoming transactions (transfers-in and QR/LMPS-in).
      - Classifies each list row as incoming/outgoing (received title or positive
        amount); only *incoming* rows are opened to read their detail/reference.
      - first run (last_ref is None): SAFEGUARD — records ONLY the newest incoming
        ref as the baseline and stops. It does not scroll and sends nothing.
      - later runs: reads top->bottom, collecting new incoming until it reaches a
        ref == last_ref (which it stops at and excludes); updates last_ref to the
        newest incoming ref.
    Returns {first_run, last_ref, new}.
    """
    d = connect(serial, password, username, log=log)
    open_messages_tab(d)
    refresh_messages(d)

    def detail_incoming(rec):
        # money received: has an "ເງິນເຂົ້າ" (amount_in) value, or the title says received
        return bool(rec.get("amount_in")) or str(rec.get("type", "")).startswith("ໄດ້ຮັບ")

    def read(cy):
        d.click(0.5, cy)
        time.sleep(1.1)
        if "titletext" in d.dump_hierarchy():
            rec = _extract_message_detail(d)
            try:
                d(text="Close").click()
            except Exception:
                d.press("back")
            time.sleep(0.7)
            return rec
        # tap didn't open a detail. Only press Back if we actually LEFT the list
        # (a stray menu/popup) — not when the tap merely missed, otherwise Back
        # would navigate away from Messages and derail the cycle.
        if not _list_rows(d):
            d.press("back")
            time.sleep(0.5)
        return None

    seen = set()
    new, new_top = [], None
    scrolls = 0
    matched = False
    guard = 0
    while not matched and guard < 120:
        guard += 1
        rows = _list_rows(d)
        nextrow = next((r for r in rows if r["sig"] not in seen), None)
        if nextrow is None:                       # nothing new on screen
            # SAFEGUARD: on first run (no watermark) never scroll — we only need
            # the newest incoming to set the baseline.
            if last_ref is None:
                break
            if scrolls >= max_scrolls:
                break
            d.swipe_ext("up", scale=0.7)
            time.sleep(1.2)
            scrolls += 1
            if all(r["sig"] in seen for r in _list_rows(d)):
                break                             # reached the bottom
            continue
        seen.add(nextrow["sig"])
        if not nextrow["incoming"]:               # skip outgoing — don't open it
            continue
        rec = read(nextrow["cy"])
        if not rec or not detail_incoming(rec):   # confirm money received
            continue
        # the newest incoming we read becomes the next watermark
        if new_top is None:
            new_top = rec["ref"]
        # SAFEGUARD: first run / no last_ref -> just record the newest incoming
        # ref as the baseline and STOP. Do not scroll, do not send any history.
        if last_ref is None:
            log(f"baseline set = {new_top}")
            matched = True
            break
        # reached a transaction we already sent last time -> STOP (do NOT include it)
        if rec["ref"] == last_ref:
            matched = True
            break
        # otherwise it's new -> collect it
        new.append(rec)

    return {"first_run": last_ref is None, "last_ref": new_top or last_ref, "new": new}


def get_messages(serial, password="", username="", max_scrolls=8, log=print):
    """Open the Messages tab and extract transaction details. Returns a list."""
    LABELS = {"ຫາບັນຊີ": "to_account", "ລາຍລະອຽດ": "details", "ເລກໃບບິນ": "bill_no",
              "ເງິນອອກ": "amount_out", "ເງິນເຂົ້າ": "amount_in", "ຈຳນວນເງິນ": "amount"}
    d = connect(serial, password, username, log=log)
    d(text='ຂໍ້ຄວາມ').click()
    time.sleep(1.5)

    def rows():
        out = []
        for el in d.xpath('//*[@clickable="true"]').all():
            m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", el.attrib.get("bounds", ""))
            if not m:
                continue
            x1, y1, x2, y2 = map(int, m.groups())
            if x1 == 0 and x2 >= 1000 and y2 < 2100 and (y2 - y1) > 150:
                out.append((y1, y2))
        return sorted(out)

    def extract():
        rec = {"type": "", "account": "", "time": "", "kind": "", "raw": []}
        pending = None
        for el in d.xpath('//*').all():
            t = (el.text or "").strip()
            rid = (el.attrib.get("resource-id") or "").split("/")[-1]
            if not t or rid in ("tab_text", "clock", "battery_percentage_view"):
                continue
            if rid == "titletext":
                rec["type"] = t
            elif rid == "source":
                rec["account"] = t
            elif rid == "time":
                rec["time"] = t
            elif rid == "subtitlehead":
                rec["kind"] = t
            elif rid == "titleprev":
                continue
            else:
                rec["raw"].append(t)
                if t in LABELS:
                    pending = LABELS[t]
                elif pending:
                    rec[pending] = t
                    pending = None
        return rec

    results, seen = [], set()
    for _ in range(max_scrolls + 1):
        rs = rows()
        if not rs:
            break
        for (y1, y2) in rs:
            cy = (y1 + y2) / 2 / 2408
            d.click(0.5, cy)
            time.sleep(1.2)
            if "titletext" not in d.dump_hierarchy():
                continue
            rec = extract()
            try:
                d(text="Close").click()
            except Exception:
                d.press("back")
            time.sleep(0.8)
            key = (rec.get("bill_no"), rec.get("time"), rec.get("type"))
            if key in seen:
                continue
            seen.add(key)
            results.append(rec)
        d.swipe_ext("up", scale=0.8)
        time.sleep(1.2)
    return results
