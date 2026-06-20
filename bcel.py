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
import xml.etree.ElementTree as ET
import uiautomator2 as u2

PKG = "com.bcel.bcelone"
HERE = os.path.dirname(os.path.abspath(__file__))


# hide the per-child console window on Windows (0 on macOS/Linux)
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


# ----------------- notification reading (pure adb, no agent) -----------------
def _adb(serial, *args, timeout=20):
    # bound the call: a per-device `adb -s <ip:port> shell ...` can hang when the
    # device drops off Wi-Fi mid-command. On timeout/failure return "".
    try:
        return subprocess.run(["adb", "-s", serial, *args],
                              capture_output=True, text=True,
                              creationflags=NO_WINDOW, timeout=timeout).stdout
    except subprocess.TimeoutExpired:
        return ""
    except Exception:
        return ""


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
    # d.set_input_ime(True)
    time.sleep(0.5)
    # Username: only entered if the login screen actually shows an account field.
    # Normally the app remembers the account, so this is skipped.
    # if username:
    #     for rid in ("username", "account", "phone", "user", "userId"):
    #         el = d(resourceId=rid)
    #         if el.exists:
    #             el.click()
    #             time.sleep(0.4)
    #             clear_and_type(d, username)
    #             log("entered username")
    #             break
    # Password: entered every time we hit the login screen.
    d.xpath('//*[@hint="ລະຫັດຜ່ານ"]').click()
    d.send_keys("", clear=True)  # ch is undefined
    
    for word in pwd:
        d.send_keys(word, clear=False)  # ch is undefined
        time.sleep(0.1)

    d.xpath('//*[@text="ເຂົ້າສູ່ລະບົບ"]').click()
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
    time.sleep(1.5)
    # A "Session expired" / network-failure popup can sit IN FRONT of the login
    # screen — dismiss it FIRST, otherwise at_login() misses it and we never
    # re-login. Retry a few times: dismissing the popup reveals the login screen.
    for _ in range(3):
        by_pass_popup_network_failure(d)
        if at_login(d):
            do_login(d, password, username, log=log)
            break
        if is_home(d) or d(text="My QR").exists:
            break
        time.sleep(1.0)
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

def by_pass_popup_network_failure(d):
    """Dismiss a blocking popup in front of the dashboard/login — network failure,
    "Session expired", etc. Tries the full-width popup button first (the common
    case), then falls back to common confirm labels. Returns True if it clicked
    something."""
    # primary: the app's standard full-width popup button
    try:
        el = d(text="ຕົກລົງ")
        if el.wait(timeout=3):
            el.click()
            time.sleep(0.8)
            return True
    except Exception:
        pass
    # fallback: a confirm/close button by label (covers the Session-expired dialog
    # when it doesn't use popupfullbutton)
    for label in ("ຕົກລົງ", "ຖືກແລ້ວ", "ຍອມຮັບ", "ປິດ", "OK", "Close"):
        try:
            el = d(text=label)
            if el.exists:
                el.click()
                time.sleep(0.2)
                return True
        except Exception:
            pass
    return False

# ----------------- actions -----------------
def create_qr(serial, amount, description, password="", username="", submit=True,
              go_home_after=True, log=print):
    """Create a 'QR with amount' and return a result dict:
        {serial, amount, description, qr_string, screenshot, home}
    If go_home_after is False, skips the final return-to-dashboard step (the
    API runs that in the background so the caller isn't blocked on it)."""
    d = connect(serial, password, username, log=log)
    poll_messages(serial, "", password, username, 6)
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


def row_source(sig):
    """Extract (from_account, from_name) the money came FROM, straight from a
    message-list row's text. The list row reliably contains this on every device
    (the detail-page layout shifts), matching the gateway's handleAutoMateTransaction:
      - QR / LMPS pipe statement: <type>|<bank>|<from-acct>|<bank>|<to-acct>|<name>|...
        -> from-acct = parts[2] (parts[5] for ONEPAY), name = parts[5]
      - regular transfer "ຈາກບັນຊີ: NAME - ACCOUNT" -> name, account
    Returns ("", "") when there is no counterparty (e.g. own-account top-ups)."""
    sig = re.sub(r"\s+", " ", sig or "").strip()
    if "|" in sig:
        # take the |-joined run, dropping any leading "...Account <own> " prefix
        tail = re.split(r"\bAccount\s+[\dxX][\dxX\-]+\s+", sig, maxsplit=1)
        tail = tail[-1]
        tail = re.sub(r"(-?\s*[\d,]+(?:\.\d+)?)\s*(LAK|USD).*$", "", tail).strip()
        parts = [p.strip() for p in tail.split("|")]
        ttype = parts[0].upper().replace(" ", "") if parts else ""
        idx = 5 if "ONEPAY" in ttype else 2
        return (parts[idx] if len(parts) > idx else ""), \
               (parts[5] if len(parts) > 5 else "")
    mf = re.search(r"ຈາກບັນຊີ:\s*(.*?)\s*(?=ລາຍລະອຽດ:|ຫາບັນຊີ:|ເລກອ້າງອິງ:|ເລກໃບບິນ:|$)", sig)
    if mf:
        val = mf.group(1).strip()
        sp = re.split(r"\s+-\s+", val, maxsplit=1)
        if len(sp) == 2:
            return sp[1].strip(), sp[0].strip()       # account, name
        return val, ""
    return "", ""


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
    # Newer app builds insert a spurious brand element at raw[2] ("OneBank Kid" or
    # a duplicate "OneBank"), pushing the real "OneBank" to raw[3] and shifting
    # every later index — breaking the gateway's fixed lookups (raw[4]=pipe/QR,
    # raw[5]="NAME\naccount", raw[9]=account). The real brand at raw[3] is the
    # tell: drop raw[2] until raw[] matches canonical [MAIN, BCEL One, OneBank,
    # MESSAGE, ...]. Old single-brand builds are unaffected.
    while len(rec["raw"]) > 3 and rec["raw"][3] == "OneBank":
        del rec["raw"][2]
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


def _wait_list_settled(d, timeout=12, poll=0.6):
    """Wait until the message list has finished (re)loading: rows are present and
    the top row is unchanged across two consecutive reads (i.e. the fetch settled).
    Returns True once stable. Used after a refresh/scroll so we don't read a list
    that is still loading on a slow connection."""
    deadline = time.time() + timeout
    prev = None
    while time.time() < deadline:
        rows = _list_rows(d)
        top = rows[0]["key"] if rows else None
        if top and top == prev:
            return True                 # non-empty and unchanged -> settled
        prev = top
        time.sleep(poll)
    return bool(_list_rows(d))


def refresh_messages(d):
    d.xpath('//*[@resource-id="titlecontext"]').click()   # tap the ↻ refresh icon
    # Wait for the reload to actually land (non-empty + stable) instead of a fixed
    # sleep — on slow internet the fetch can still be in flight after 2s, leaving
    # the list empty/loading and making the poll read nothing.
    _wait_list_settled(d)
    # Scroll back to the very top so every cycle reads from the NEWEST message.
    # Without this, a list left scrolled-down from the previous cycle makes the
    # poll read older transactions (ref != last_ref) as if they were new and keep
    # scrolling. Swiping "down" reveals content above (newest is first).
    for _ in range(3):
        d.swipe_ext("down", scale=0.8)
        time.sleep(0.3)
    # let the top settle after scrolling before the poll reads it
    _wait_list_settled(d, timeout=4)


def _list_rows(d):
    """Message rows currently on screen, top->bottom. Each row is classified as
    incoming/outgoing from its amount sign in the list (outgoing = red, '-').

    Bounds are matched RELATIVE to the screen size (not fixed pixels) so the
    same logic works across devices with different resolutions — earlier fixed
    thresholds (y2 < 2100) silently dropped rows on taller/denser screens, which
    made the list look empty and the poller just kept scrolling."""
    info = d.info
    h = info.get("displayHeight", 2408)
    w = info.get("displayWidth", 1080)

    # ONE hierarchy snapshot per call (not three separate xpath dumps): faster over
    # Wi-Fi and — crucially — a single consistent view so the viewport, the
    # clickable rows, and their text can't disagree because the list shifted
    # between dumps (which previously caused off-by-one taps).
    try:
        root = ET.fromstring(d.dump_hierarchy())
    except Exception:
        return []
    parsed = []   # (x1, y1, x2, y2, attrib) for every node with valid bounds
    for el in root.iter("node"):
        a = el.attrib
        m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", a.get("bounds", ""))
        if m:
            x1, y1, x2, y2 = map(int, m.groups())
            parsed.append((x1, y1, x2, y2, a))

    # Visible list area = the scrollable list container's bounds. Rows that hang
    # below it are only partly on screen, and their center can land on the bottom
    # tab bar — so we use the container bottom as a hard cutoff for clicks.
    view_top, view_bottom = h * 0.09, h * 0.95
    for (x1, y1, x2, y2, a) in parsed:
        if a.get("scrollable") == "true" and (x2 - x1) >= w * 0.9 and (y2 - y1) > h * 0.2:
            view_top, view_bottom = max(view_top, y1), min(view_bottom, y2)
            break

    conts = []
    for (x1, y1, x2, y2, a) in parsed:
        if a.get("clickable") != "true":
            continue
        cy = (y1 + y2) // 2
        # Full-width rows whose CENTER is inside the visible list viewport (above
        # the bottom tab bar, below the header). A row hanging past the fold is
        # skipped until it scrolls fully into view, so a click never hits a tab.
        if (x1 <= w * 0.03 and x2 >= w * 0.9 and (y2 - y1) > h * 0.04
                and view_top <= cy <= view_bottom):
            # keep each row's absolute-pixel center so we can click the row by
            # index at its real position instead of guessing coordinates.
            conts.append((y1, y2, ((x1 + x2) // 2, cy)))
    conts.sort(key=lambda c: c[0])
    texts = []
    for (x1, y1, x2, y2, a) in parsed:
        t = (a.get("text") or "").strip()
        if t:
            texts.append(((y1 + y2) // 2, t))
    rows = []
    for (y1, y2, center) in conts:
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
        # Stable dedup key from regex-extracted fields (kind + timestamp + amount).
        # Unlike the full sig, this does NOT change when a row is partly clipped at
        # the scroll edge, so an already-clicked message isn't re-opened/re-counted
        # after scrolling.
        tmatch = re.search(r"\b(\d{1,2}:\d{2}:\d{2})\b", sig)
        key = "|".join((kind, tmatch.group(1) if tmatch else "",
                        am.group(0).strip() if am else ""))
        rows.append({"cy": (y1 + y2) / 2 / h, "center": center, "key": key,
                     "sig": sig, "kind": kind, "incoming": incoming})
    return rows


def _amount_value(text):
    """Numeric value of an amount, ignoring formatting. The list shows '2,000 LAK'
    while the detail shows '2,000.00 LAK' — both normalize to 2000.0."""
    m = re.search(r"(-?\s*[\d,]+(?:\.\d+)?)\s*(?:LAK|USD)", text or "")
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", "").replace(" ", ""))
    except ValueError:
        return None


def _hhmmss(text):
    m = re.search(r"\b(\d{1,2}:\d{2}:\d{2})\b", text or "")
    return m.group(1) if m else ""


def detail_matches_row(row_sig, rec):
    """Verify the opened detail belongs to the row that was clicked, by comparing
    the amount and the HH:MM:SS timestamp that appear in BOTH the list row and the
    detail. A *conflict* on either means the tap opened the wrong transaction
    (off-by-one, list shifted mid-tap); we require at least one positive match and
    zero conflicts. This guards against recording the wrong amount/ref."""
    ra = _amount_value(row_sig)
    da = _amount_value(rec.get("amount_in") or rec.get("amount") or "")
    rt, dt = _hhmmss(row_sig), _hhmmss(rec.get("time") or "")
    if ra is not None and da is not None and ra != da:
        return False                      # amount conflict -> wrong detail
    if rt and dt and rt != dt:
        return False                      # time conflict -> wrong detail
    return bool((ra is not None and da is not None and ra == da) or (rt and dt and rt == dt))


def poll_messages(serial, last_ref=None, password="", username="", max_scrolls=6,
                  kinds=None, fresh=False, log=print):
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
    d = connect(serial, password, username, log=log, fresh=fresh)
    by_pass_popup_network_failure(d)
    open_messages_tab(d)
    refresh_messages(d)

    def detail_incoming(rec):
        # money received: has an "ເງິນເຂົ້າ" (amount_in) value, or the title says received
        return bool(rec.get("amount_in")) or str(rec.get("type", "")).startswith("ໄດ້ຮັບ")

    def read(row):
        # Open a row's detail, WAIT until it has fully rendered, then VERIFY it's
        # the one we clicked (amount + time must match the list row). Two hazards
        # under slow internet / slow app:
        #   1) the detail opens late -> never read the list as if the tap missed;
        #   2) the detail renders half-loaded -> the bill number isn't there yet and
        #      we'd record a fallback ref. So we poll for the detail AND its value
        #      fields (amount) before accepting, up to a timeout, instead of a fixed
        #      sleep. On mismatch/incomplete we retry; we never return partial/wrong.
        for attempt in range(3):
            # re-locate the row by its stable key (its pixel center may have moved)
            cur = next((r for r in _list_rows(d) if r["key"] == row["key"]), None)
            if cur is None:
                return None                       # row scrolled away — let caller move on
            d.click(*cur["center"])
            rec, opened = None, False
            deadline = time.time() + 10           # wait up to 10s for a slow load
            while time.time() < deadline:
                time.sleep(0.5)
                r = _extract_message_detail(d)
                if r.get("type"):                 # detail container is on screen
                    opened = True
                    if r.get("amount_in") or r.get("amount"):
                        rec = r                   # value fields rendered -> fully loaded
                        break
            if opened:                            # close the detail we opened
                try:
                    d(text="Close").click()
                except Exception:
                    d.press("back")
                time.sleep(0.6)
            if rec and detail_matches_row(row["sig"], rec):
                return rec                        # verified + complete
            log(f"detail not ready/mismatch for {row['key']} (attempt {attempt + 1}/3) — retrying")
            # if the tap never opened a detail and we left the list, get back to it
            if not opened and not _list_rows(d):
                d.press("back")
                time.sleep(0.5)
        log(f"⚠ could not read a complete matching detail for {row['key']} — skipped this cycle")
        return None

    seen = set()           # row keys already handled (stable across scroll)
    done_refs = set()      # detail refs already collected — guards double-send
    new, new_top = [], None
    scrolls = 0
    matched = False
    guard = 0
    while not matched and guard < 120:
        guard += 1
        rows = _list_rows(d)
        nextrow = next((r for r in rows if r["key"] not in seen), None)
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
            if all(r["key"] in seen for r in _list_rows(d)):
                break                             # reached the bottom
            continue
        seen.add(nextrow["key"])
        if not nextrow["incoming"]:               # skip outgoing — don't open it
            continue
        rec = read(nextrow)                       # open detail -> real ref/bill_no
        if not rec or not detail_incoming(rec):   # confirm money received
            continue
        # reached a transaction we already sent last time -> STOP (do NOT include it)
        if last_ref is not None and rec["ref"] == last_ref:
            matched = True
            break
        # already collected this ref in THIS poll (e.g. re-read after a scroll) ->
        # skip so it can never be sent twice
        if rec["ref"] in done_refs:
            continue
        done_refs.add(rec["ref"])
        # source account comes from the reliable list-row text, not the detail
        # page (whose field order shifts across devices).
        fa, fn = row_source(nextrow["sig"])
        if fa or fn:
            rec["from_account"], rec["from_name"] = fa, fn
        # the newest incoming we read becomes the next watermark
        if new_top is None:
            new_top = rec["ref"]
        # SAFEGUARD: first run / no last_ref -> just record the newest incoming
        # ref as the baseline and STOP. Do not scroll, do not send any history.
        if last_ref is None:
            log(f"baseline set = {new_top}")
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
    by_pass_popup_network_failure(d)
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
