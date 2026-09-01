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
from decimal import Decimal, InvalidOperation
import uiautomator2 as u2

PKG = "com.bcel.bcelone"
HERE = os.path.dirname(os.path.abspath(__file__))


class MessagesUIError(RuntimeError):
    """Messages cannot be navigated safely; stop rather than guessing actions."""


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


def clear_field(d, n=16):
    """Clear the focused field: jump to end, then backspace n times."""
    d.shell("input keyevent 123 " + ("67 " * n))


def type_via_input(d, value, per_char_delay=0.1):
    """Type into the FOCUSED field using the device's own keyboard via
    `adb shell input text`, one character at a time. This needs NO special IME —
    it's the fallback for devices where FastInputIME can't be enabled (some
    MIUI/Vivo/Oppo and locked-down Android builds). The list form of d.shell
    avoids shell-escaping issues; space is the one char `input text` treats
    specially (encoded as %s)."""
    for ch in str(value):
        d.shell(["input", "text", "%s" if ch == " " else ch])
        time.sleep(per_char_delay)


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
_PASSWORD_INPUT_XPATH = (
    f'//*[@package="{PKG}" and '
    '(@hint="ລະຫັດຜ່ານ" or @password="true")]'
)


def password_input(d, timeout=15):
    """Password field across BCEL/WebView variants.

    Some Android 16 / WebView builds expose the secure EditText but omit its HTML
    hint. Select by the semantic password attribute as a fallback; never by screen
    coordinates.
    """
    field = d.xpath(_PASSWORD_INPUT_XPATH)
    if not field.wait(timeout=timeout):
        raise RuntimeError("BCEL password input is not exposed to accessibility")
    return field


def at_login(d):
    if d.app_current().get("activity", "").endswith("BcelOneLogin"):
        return True
    return d(resourceId="login").exists


def enter_password(d, field, pwd, log=print):
    """Type the password into `field`, trying input methods in order of preference
    and falling through on failure. The device's IME support varies:

    1. FastInputIME (AdbKeyboard) — fast and hides the on-screen keyboard, and some
       Android 16 / WebView builds only accept text through it. But installing it
       is blocked on some OEM/locked-down builds, where set_input_ime() raises
       "install AdbKeyboard ime failed".
    2. Plain d.send_keys() — u2's own graceful path: it uses the accessibility
       set_text when the IME isn't installed. This is what worked before the
       explicit set_input_ime() was added, so it's the key regression-proof step.
    3. `adb shell input text` — types via whatever keyboard is active, no special
       IME at all. Last-resort for WebView fields that reject set_text.

    Whichever succeeds first wins; only if ALL fail do we raise.
    """
    def _via_fastime():
        d.set_input_ime(True)
        time.sleep(0.5)
        try:
            d.send_keys("", clear=True)
            for ch in pwd:
                d.send_keys(ch, clear=False)
                time.sleep(0.1)
        finally:
            try:
                d.set_input_ime(False)
            except Exception:
                pass

    def _via_sendkeys():
        d.send_keys("", clear=True)
        for ch in pwd:
            d.send_keys(ch, clear=False)
            time.sleep(0.1)

    def _via_inputtext():
        clear_field(d)
        type_via_input(d, pwd)

    last_err = None
    for strat in (_via_fastime, _via_sendkeys, _via_inputtext):
        try:
            field.click()
            time.sleep(0.3)
            strat()
            return strat.__name__
        except Exception as e:
            last_err = e
            log(f"⚠ password entry via {strat.__name__} failed ({e}) — trying next method")
    raise RuntimeError(f"could not enter password by any input method: {last_err}")


def do_login(d, pwd, username="", log=print):
    if not pwd:
        raise RuntimeError("login required but no password provided")
    log("login screen detected — signing in")
    field = password_input(d)
    enter_password(d, field, pwd, log=log)

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
_AMOUNT_TOKEN = r"([−-]?\s*[\d,]+(?:\.\d+)?)\s*([A-Z]{3})\b"
_AMOUNT_RE = re.compile(_AMOUNT_TOKEN)


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
        tail = re.sub(rf"{_AMOUNT_TOKEN}.*$", "", tail).strip()
        parts = [p.strip() for p in tail.split("|")]
        ttype = parts[0].upper().replace(" ", "") if parts else ""
        idx = 5 if "ONEPAY" in ttype else 2
        return (parts[idx] if len(parts) > idx else ""), \
               (parts[5] if len(parts) > 5 else "")
    mf = re.search(
        r"ຈາກບັນຊີ:\s*(.*?)\s*"
        rf"(?={_AMOUNT_TOKEN}|ລາຍລະອຽດ:|"
        r"ຫາບັນຊີ:|ເລກອ້າງອິງ:|ເລກໃບບິນ:|$)",
        sig,
    )
    if mf:
        val = mf.group(1).strip()
        sp = re.split(r"\s+-\s+", val, maxsplit=1)
        if len(sp) == 2:
            return sp[1].strip(), sp[0].strip()       # account, name
        return val, ""
    return "", ""


def detail_source(rec):
    """(from_account, from_name) from the VERIFIED detail record's raw[], anchored
    on the "ຈາກບັນຊີ" label / the pipe statement (NOT a fixed index and NOT the
    list-row text). This keeps the sender name consistent with the SAME transaction
    whose amount/ref we read from the detail — the previous approach pulled the name
    from a separate list-row snapshot, so a stale/bled row could attach the wrong
    sender (e.g. user2's transfer showing user1's name)."""
    raw = rec.get("raw", []) or []
    # QR / LMPS pipe statement: <type>|<bank>|<from-acct>|<bank>|<to-acct>|<name>|...
    for seg in raw:
        if "|" in seg:
            parts = [p.strip() for p in seg.split("|")]
            ttype = parts[0].upper().replace(" ", "") if parts else ""
            idx = 5 if "ONEPAY" in ttype else 2
            return (parts[idx] if len(parts) > idx else ""), \
                   (parts[5] if len(parts) > 5 else "")
    # regular transfer: the value ("NAME\naccount") follows the "ຈາກບັນຊີ" label
    for i, seg in enumerate(raw):
        s = seg.strip()
        if s.rstrip(":") == "ຈາກບັນຊີ":                 # pure label -> value is next
            val = raw[i + 1].strip() if i + 1 < len(raw) else ""
        elif s.startswith("ຈາກບັນຊີ") and len(s) > len("ຈາກບັນຊີ") + 1:
            val = s[len("ຈາກບັນຊີ"):].lstrip(": ").strip()  # label+value in one node
        else:
            continue
        sp = [p for p in re.split(r"\n|\s+-\s+", val, maxsplit=1)]
        if len(sp) > 1:
            return sp[1].strip(), sp[0].strip()          # account, name
        return val, ""
    return "", ""


def _normalized_name(value):
    """Comparable sender name without changing the value sent to the gateway."""
    return re.sub(r"\s+", " ", value or "").strip().casefold()


def _accounts_conflict(left, right):
    """Return True only when two account values are clearly different.

    BCEL sometimes masks one representation, so an incomplete/masked value is not
    enough evidence of a conflict. When both expose at least four digits, the last
    four must agree.
    """
    ld = re.sub(r"\D", "", left or "")
    rd = re.sub(r"\D", "", right or "")
    return len(ld) >= 4 and len(rd) >= 4 and ld[-4:] != rd[-4:]


def _detail_fingerprint(rec):
    """Fields that must stop changing before a detail record is accepted."""
    return (
        rec.get("type", ""), rec.get("kind", ""), rec.get("time", ""),
        rec.get("bill_no", ""), rec.get("amount_in", ""), rec.get("amount", ""),
        tuple(rec.get("raw", []) or []),
    )


def _extract_message_detail(d):
    """Read the open message-detail screen into a dict, incl. a unique 'ref'."""
    root = _message_snapshot(d)
    frame = _new_message_frame(root)
    if frame is not None:
        return _extract_new_detail(frame)
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


def _row_is_incoming(kind, sig, positive_amount):
    """Classify incoming money while excluding card purchase/sale notices.

    SAL/Mastercard rows can show an unsigned positive amount even though their
    detail is a card debit or failed purchase ("ເງິນອອກ"). They are not bank
    transfer income and must never block the incoming-transaction watermark.
    """
    if kind == "SAL" or "mastercard" in (sig or "").casefold():
        return False
    return "ໄດ້ຮັບ" in (sig or "") or positive_amount


def _poll_scroll_limit(last_ref, configured_limit):
    """Use a deeper, one-time search only while establishing the baseline."""
    return max(configured_limit, 30) if last_ref is None else configured_limit


def _ddmmyyyy(text):
    match = re.search(r"\b(\d{2}/\d{2}/\d{4})\b", text or "")
    return match.group(1) if match else ""


_MESSAGE_KINDS = {"TRI", "TRO", "ACC", "SAL", "TOP", "TFO"}
_NEW_REFRESH = '//*[@resource-id="messageframe"]//*[@content-desc="Context Icon"]'
_NEW_TAB = '//*[@resource-id="bottomtab"]//*[@content-desc="Message"]'


def _message_snapshot(d):
    try:
        return ET.fromstring(d.dump_hierarchy())
    except (ET.ParseError, TypeError):
        return None


def _new_message_frame(root):
    if root is None:
        return None
    frames = [n for n in root.iter("node") if n.get("resource-id") == "messageframe"]
    if len(frames) != 1:
        return None
    frame = frames[0]
    if any(n.get("resource-id") in ("titlecontext", "titletext") for n in frame.iter("node")):
        return None                         # keep the legacy parser unchanged
    return frame


def _texts(node):
    return [(n.get("text") or "").strip() for n in node.iter("node")
            if (n.get("text") or "").strip()]


def _new_detail_panel(frame):
    if frame is None:
        return None
    parents = {child: parent for parent in frame.iter() for child in parent}
    panels = []
    for nav in frame.iter("node"):
        if (nav.get("class", "").endswith("NavigationView")
                and any(n.get("content-desc") == "Close" for n in nav.iter("node"))):
            panel = parents.get(nav)
            if panel is not None and any(n.get("text") in _MESSAGE_KINDS for n in panel):
                panels.append(panel)
    return panels[0] if len(panels) == 1 else None


def _extract_new_detail(frame):
    empty = {"type": "", "account": "", "time": "", "kind": "", "raw": [], "ref": ""}
    panel = _new_detail_panel(frame)
    if panel is None:
        return empty
    # Only direct fields of the OPEN panel, never the retained list behind it.
    nav = next(n for n in panel if n.get("class", "").endswith("NavigationView"))
    headings = _texts(nav)
    fields = [(n.get("text") or "").strip() for n in panel if (n.get("text") or "").strip()]
    kind = next((t for t in fields if t in _MESSAGE_KINDS), "")
    account = next((t for t in fields if t.startswith("Account ")), "")
    timestamp = next((t for t in fields if re.fullmatch(r"\d{2}/\d{2}/\d{4}\s+\d{1,2}:\d{2}:\d{2}", t)), "")
    labels = {**_MSG_LABELS, "ຈາກບັນຊີ": "sender"}
    values = {}
    for i, text in enumerate(fields):
        if text in labels:
            key = labels[text]
            if key in values:
                return empty                 # ambiguous/transitioning panel
            if i + 1 < len(fields) and fields[i + 1] not in labels:
                values[key] = fields[i + 1]
    bill = values.get("bill_no", "")
    amount = values.get("amount_in") or values.get("amount") or values.get("amount_out")
    pipes = [t for t in fields if "|" in t and
             t.split("|", 1)[0].upper().replace(" ", "").startswith(("LMPSQRTRANSFERIN", "ONEPAY"))]
    pipe = pipes[0] if len(pipes) == 1 else ""
    source_account, source_name = row_source(pipe) if pipe else ("", "")
    pipe_complete = len(pipe.split("|")) >= 6 and source_account and source_name
    if not (len(headings) == 1 and kind and account and timestamp
            and re.fullmatch(r"[A-Za-z0-9-]+", bill)
            and _amount_identity(amount) and (values.get("sender") or pipe_complete)):
        return empty                         # never generate a fallback ref for this layout
    if pipe_complete:
        values.setdefault("details", pipe)
    rec = {"type": headings[0], "account": account, "time": timestamp,
           "kind": kind, "ref": bill, **values}
    # Adapt the new labelled panel to the gateway's existing fixed-index contract.
    # These prefix strings are protocol metadata; all transaction values come
    # from this one detail panel. In particular raw[9] is an account, not a bill.
    raw = ["MAIN", "BCEL One", "OneBank", "MESSAGE",
           "ຈາກບັນຊີ" if values.get("sender") else pipe,
           values.get("sender") or f"{source_name}\n{source_account}",
           "ລາຍລະອຽດ", values.get("details", ""), "ຫາບັນຊີ",
           values.get("to_account") or account.removeprefix("Account ").strip(),
           "ເລກໃບບິນ", bill]
    for label, key in _MSG_LABELS.items():
        if key in ("amount_in", "amount_out", "amount") and key in values:
            raw.extend([label, values[key]])
    rec["raw"] = raw
    return rec


def _row_record(sig, center, height):
    km = re.search(r"\b(TRI|TRO|ACC|SAL|TOP|TFO)\b", sig)
    kind = km.group(1) if km else ""
    am = _AMOUNT_RE.search(sig)
    positive = bool(am) and am.group(1).lstrip()[:1] not in ("-", "−")
    timestamp = _row_timestamp(sig)
    key = "|".join((kind, timestamp, am.group(0).strip() if am else ""))
    return {"cy": center[1] / height, "center": center, "key": key, "sig": sig,
            "kind": kind, "incoming": _row_is_incoming(kind, sig, positive)}


def _new_list_rows(frame, info):
    # The updated WebView retains the previous list while showing detail.
    if any(n.get("content-desc") == "Close" for n in frame.iter("node")):
        return []
    parents = {child: parent for parent in frame.iter() for child in parent}
    scrolls = [n for n in frame.iter("node") if n.get("scrollable") == "true"]
    if len(scrolls) != 1:
        return []
    scroll = scrolls[0]
    panel = parents.get(scroll)
    if panel is None or not any(
            n.get("class", "").endswith("NavigationView") and "ຂໍ້ຄວາມ" in _texts(n)
            and any(c.get("content-desc") == "Context Icon" for c in n.iter("node"))
            for n in panel):
        return []
    viewport = _parse_bounds(scroll.get("bounds"))
    if not viewport:
        return []
    rows = []
    for index, node in enumerate(scroll, 1):
        texts = _texts(node)
        if sum(t in _MESSAGE_KINDS for t in texts) != 1:
            continue
        bounds = _parse_bounds(node.get("bounds"))
        if not bounds:
            continue
        x1, y1, x2, y2 = bounds
        center = ((x1 + x2) // 2, (y1 + y2) // 2)
        if not (viewport[1] <= center[1] < viewport[3]):
            continue
        row = _row_record("\n".join(texts), center, info.get("displayHeight", 2408))
        # Use the actual accessible row node, not guessed coordinates or a
        # clickable-flag override. Detail verification still guards list changes.
        row["xpath"] = f'(//*[@resource-id="messageframe"]//*[@scrollable="true"])[1]/*[{index}]'
        rows.append(row)
    return rows


def _message_list_visible(d):
    """True only for the real Messages list, not detail-shaped full-width nodes."""
    if d.xpath('//*[@resource-id="titlecontext"]').exists:
        return bool(_list_rows(d))
    frame = _new_message_frame(_message_snapshot(d))
    return frame is not None and bool(_new_list_rows(frame, d.info))


def _message_detail_visible(d):
    # A missing/empty list is NOT proof that a detail is open. Require both
    # detail-specific markers in the foreground bank app before navigating back.
    if d.app_current().get("package") != PKG:
        return False
    if (d.xpath('//*[@resource-id="titletext"]').exists
            and d.xpath('//*[@resource-id="subtitlehead"]').exists):
        return True
    return _new_detail_panel(_new_message_frame(_message_snapshot(d))) is not None


def _wait_for_message_list(d, timeout=15, poll=0.5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _message_list_visible(d):
            return True
        time.sleep(poll)
    return _message_list_visible(d)


def close_message_detail(d, timeout=5, poll=0.2):
    """Close a positively identified detail once, never back out of a loading list."""
    if _message_list_visible(d):
        return True
    if not _message_detail_visible(d):
        return False
    if _new_detail_panel(_new_message_frame(_message_snapshot(d))) is not None:
        # This named control was verified on the real device. No coordinate or
        # Back-key fallback if its accessibility action fails.
        try:
            d(description="Close").click(timeout=3)
        except Exception:
            return False
        return _wait_for_message_list(d, timeout=timeout, poll=poll)

    controls = (
        lambda: d.xpath('//*[@resource-id="titleprev"]'),
        lambda: d(text="Close"),
    )
    for get_control in controls:
        try:
            control = get_control()
            if not control.exists:
                continue
            control.click()
        except Exception:
            # The click may have reached the device before the transport failed.
            # Don't follow it with another close/back on an unknown screen.
            return False
        return _wait_for_message_list(d, timeout=timeout, poll=poll)

    if not _message_detail_visible(d):
        return False
    try:
        d.press("back")
    except Exception:
        return False
    return _wait_for_message_list(d, timeout=timeout, poll=poll)


def open_messages_tab(d):
    print(d.dump_hierarchy())
    # Navigate using the actual tab, not a sequence of Back presses that can
    # exit a changed UI. connect() is responsible for reaching a logged-in app.
    tab = d.xpath(_NEW_TAB)
    if tab.exists:
        tab.click()
    elif not d(text="ຂໍ້ຄວາມ").wait(timeout=15):
        raise MessagesUIError("Messages tab not found — check BCEL login and UI accessibility")
    else:
        d(text="ຂໍ້ຄວາມ").click()
    if _wait_for_message_list(d):
        return
    if _message_detail_visible(d) and close_message_detail(d):
        return
    raise MessagesUIError(
        "Messages list unavailable after waiting — it may be loading, empty, "
        "changed, or inaccessible; no further navigation attempted"
    )


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


def _scroll_messages_to_top(d, max_swipes=30):
    """Swipe toward newer rows until the visible top row stops changing."""
    previous_top = None
    stable_reads = 0
    for _ in range(max_swipes):
        rows = _list_rows(d)
        if not rows:
            return False                       # never swipe an unidentified screen
        top = rows[0]["key"]
        if top and top == previous_top:
            stable_reads += 1
            if stable_reads >= 2:
                return True
        else:
            stable_reads = 0
        previous_top = top
        d.swipe_ext("down", scale=0.8)
        time.sleep(0.3)
    return False


def refresh_messages(d):
    if not _message_list_visible(d):
        raise MessagesUIError("Messages list unavailable before refresh")
    refresh = d.xpath('//*[@resource-id="titlecontext"]')
    if not refresh.exists:
        refresh = d.xpath(_NEW_REFRESH)
    refresh.click()                            # named refresh control, no coordinate fallback
    # Wait for the reload to actually land (non-empty + stable) instead of a fixed
    # sleep — on slow internet the fetch can still be in flight after 2s, leaving
    # the list empty/loading and making the poll read nothing.
    if not _wait_list_settled(d):
        raise MessagesUIError("Messages list did not load after refresh — no scrolling attempted")
    # Scroll back to the true top so every cycle starts from the NEWEST message.
    # A fixed number of swipes is unsafe after a deep history scan: the monitor
    # can otherwise revisit older rows and treat them as new because their ref is
    # different from the current watermark.
    if not _scroll_messages_to_top(d):
        raise MessagesUIError("Messages list could not be safely scrolled to the top")
    # let the top settle after scrolling before the poll reads it
    if not _wait_list_settled(d, timeout=4):
        raise MessagesUIError("Messages list unavailable after scrolling")


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
    frame = _new_message_frame(root)
    if frame is not None:
        return _new_list_rows(frame, info)
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
        # Keep UI nodes separated without inventing a pipe. A literal `|` has
        # banking meaning in QR/LMPS statements, and row_source() uses it to choose
        # that parser. Joining ordinary transfer fields with `|` made TRI rows look
        # like QR statements and could select an unrelated/previous sender field.
        sig = "\n".join(rt)
        # the row's leading badge text is the kind code (TRI/TRO/ACC/SAL/TOP)
        km = re.search(r"\b(TRI|TRO|ACC|SAL|TOP|TFO)\b", sig)
        kind = km.group(1) if km else ""
        # "incoming" = money received. Detect by the title ("ໄດ້ຮັບ" = received)
        # or a positive amount (outgoing amounts are red and start with '-').
        # This catches BOTH transfers-in (TRI, ໄດ້ຮັບເງິນໂອນ) and QR/LMPS
        # payments-in (ACC, ໄດ້ຮັບເງິນ) — kind alone is not enough since ACC is
        # used for both incoming and outgoing.
        am = _AMOUNT_RE.search(sig)
        positive = bool(am) and am.group(1).lstrip()[:1] not in ("-", "−")
        incoming = _row_is_incoming(kind, sig, positive)
        # Stable dedup key from regex-extracted fields (kind + timestamp + amount).
        # Unlike the full sig, this does NOT change when a row is partly clipped at
        # the scroll edge, so an already-clicked message isn't re-opened/re-counted
        # after scrolling.
        timestamp = _row_timestamp(sig)
        key = "|".join((kind, timestamp,
                        am.group(0).strip() if am else ""))
        rows.append({"cy": (y1 + y2) / 2 / h, "center": center, "key": key,
                     "sig": sig, "kind": kind, "incoming": incoming})
    return rows


def _amount_identity(text):
    """Exact numeric value and currency used to match a row to its detail."""
    m = _AMOUNT_RE.search(text or "")
    if not m:
        return None
    try:
        number = m.group(1).replace(",", "").replace(" ", "").replace("−", "-")
        return Decimal(number), m.group(2)
    except InvalidOperation:
        return None


def _hhmmss(text):
    m = re.search(r"\b(\d{1,2}:\d{2}:\d{2})\b", text or "")
    return m.group(1) if m else ""


def _row_timestamp(sig):
    """Read the message's timestamp field, never a time embedded in QR text."""
    fields = [line.strip() for line in (sig or "").splitlines()]
    times = [field for field in fields if re.fullmatch(
        r"(?:\d{2}/\d{2}/\d{4}\s+)?\d{1,2}:\d{2}:\d{2}", field)]
    if len(times) == 1:
        return _hhmmss(times[0])
    if times:
        return ""                            # ambiguous timestamp fields
    dates = [field for field in fields if re.fullmatch(r"\d{2}/\d{2}/\d{4}", field)]
    return dates[0] if len(dates) == 1 else ""


def detail_matches_row(row_sig, rec):
    """Verify the opened detail belongs to the row that was clicked, by comparing
    the amount, HH:MM:SS timestamp, and sender that appear in BOTH the list row and
    detail. Amount and time are mandatory; accepting only one lets a half-rendered
    WebView record combine the new transaction's ref/amount with the previous
    transaction's sender. Sender/account conflicts are also rejected, but a source
    omitted by one side is allowed for transaction types that do not expose it."""
    ra = _amount_identity(row_sig)
    da = _amount_identity(rec.get("amount_in") or rec.get("amount") or "")
    detail_time = rec.get("time") or ""
    timestamp = _row_timestamp(row_sig)
    rt, dt = _hhmmss(timestamp), _hhmmss(detail_time)
    if ra is None or da is None or ra != da:
        return False                      # absent/conflicting amount -> incomplete/wrong
    if rt:
        if not dt or rt != dt:
            return False                  # present time must agree exactly
    else:
        rd, dd = _ddmmyyyy(timestamp), _ddmmyyyy(detail_time)
        if not rd or not dd or rd != dd:
            return False                  # older rows expose only the transaction date

    row_account, row_name = row_source(row_sig)
    detail_account, detail_name = detail_source(rec)
    if (row_name and detail_name
            and _normalized_name(row_name) != _normalized_name(detail_name)):
        return False                      # sender-name bleed from a different row/detail
    if _accounts_conflict(row_account, detail_account):
        return False                      # clearly different source account
    return True


def _read_verified_detail(d, row, log=print):
    """Open one row and return (stable detail, exact verified row signature).

    The WebView can expose old and new values in the same transition, so a record
    is accepted only after three identical complete snapshots match the current
    list row. A mismatch is retried without returning partial transaction data.
    """
    for attempt in range(3):
        # Re-locate the row by its stable key (its pixel center may have moved).
        cur = next((r for r in _list_rows(d) if r["key"] == row["key"]), None)
        if cur is None:
            return None                           # row scrolled away
        if cur.get("xpath"):
            d.xpath(cur["xpath"]).click()
        else:
            d.click(*cur["center"])
        rec, opened = None, False
        stable_fingerprint, stable_reads = None, 0
        deadline = time.time() + 10               # wait up to 10s for a slow load
        while time.time() < deadline:
            time.sleep(0.5)
            r = _extract_message_detail(d)
            if r.get("type"):                    # detail container is on screen
                opened = True
                # A WebView can briefly expose a mixed tree while navigating:
                # new amount/ref with the previous sender name. Require the
                # entire relevant detail to be unchanged across three reads,
                # and compare it with the CURRENT row snapshot before accepting.
                if ((r.get("amount_in") or r.get("amount"))
                        and detail_matches_row(cur["sig"], r)):
                    fingerprint = _detail_fingerprint(r)
                    if fingerprint == stable_fingerprint:
                        stable_reads += 1
                    else:
                        stable_fingerprint, stable_reads = fingerprint, 1
                    if stable_reads >= 3:
                        rec = r
                        break
                else:
                    stable_fingerprint, stable_reads = None, 0
        # Incomplete data can still be a recognized detail panel. Close that
        # panel safely before retrying; never mistake missing fields for a list.
        if not opened:
            opened = _message_detail_visible(d)
        if opened and not close_message_detail(d):
            log("⚠ detail close failed — message list was not restored")
            return None
        if rec:
            # Preserve the exact row snapshot used for verification. The list may
            # have refreshed since the row was first selected.
            return rec, cur["sig"]
        log(f"detail not ready/mismatch for {row['key']} (attempt {attempt + 1}/3) — retrying")
        # Without a recognized detail or list, Back may exit the bank app.
        if not opened and not _list_rows(d):
            raise MessagesUIError("Messages list unavailable after opening a row — no Back pressed")
    log(f"⚠ could not read a complete matching detail for {row['key']} — skipped this cycle")
    return None


def poll_messages(serial, last_ref=None, password="", username="", max_scrolls=6,
                  kinds=None, fresh=False, log=print, pending_scans=None):
    """Incremental poll for incoming transactions (transfers-in and QR/LMPS-in).
      - Classifies each list row as incoming/outgoing (received title or positive
        amount); only *incoming* rows are opened to read their detail/reference.
      - first run (last_ref is None): SAFEGUARD — records ONLY the newest incoming
        ref as the baseline and stops. It may scroll past card/outgoing rows to find
        that first incoming row, but sends nothing.
      - later runs: reads top->bottom, collecting new incoming until it reaches a
        ref == last_ref (which it stops at and excludes); updates last_ref to the
        newest incoming ref.
    Returns {first_run, last_ref, new}.
    """
    d = connect(serial, password, username, log=log, fresh=fresh)
    by_pass_popup_network_failure(d)
    open_messages_tab(d)
    refresh_messages(d)

    result = _scan_messages(d, last_ref, max_scrolls, log)
    pending = list(pending_scans or [])
    # Retry only one unfinished range per cycle. Seek to its failed row without
    # reopening the verified history above it. New-message scanning has its own
    # boundary and does not depend on delivery or this older range succeeding.
    if pending:
        task = pending.pop(0)
        if not _scroll_messages_to_top(d) or not _wait_list_settled(d):
            raise MessagesUIError("Messages list unavailable before pending-detail retry")
        retry = _scan_messages(d, task["last_ref"], max_scrolls, log,
                               start_row=task["start_row"])
        refs = {r["ref"] for r in result["new"]}
        result["new"].extend(r for r in retry["new"] if r["ref"] not in refs)
        if retry["pending_scan"]:
            pending.append(retry["pending_scan"])
    # With no verified head yet, keep the old main boundary and retry normally.
    # In particular a failed first-run baseline must never send history.
    if result["pending_scan"] and result["scan_ref"] != last_ref:
        pending.append(result["pending_scan"])
    result["pending_scans"] = pending
    return result


def _scan_messages(d, last_ref, max_scrolls, log, start_row=None):
    """Scan a new range, or seek directly to an unfinished range's first row."""

    def detail_incoming(rec):
        # money received: has an "ເງິນເຂົ້າ" (amount_in) value, or the title says received
        return bool(rec.get("amount_in")) or str(rec.get("type", "")).startswith("ໄດ້ຮັບ")

    seen = set()           # row keys already handled (stable across scroll)
    done_refs = set()      # detail refs already collected — guards double-send
    new, new_top = [], None
    scrolls = 0
    # A newly-added account may have many SAL/Mastercard notices above its newest
    # actual incoming transfer. Search deeper only while establishing the no-send
    # baseline; normal incremental polls keep the caller's smaller scroll limit.
    scroll_limit = _poll_scroll_limit(last_ref, max_scrolls)
    matched = False
    blocked = False        # an incoming row could not be verified; hold old watermark
    failed_row = None
    last_verified_row = None
    seeking = start_row is not None
    reached_bottom = False
    guard = 0
    while not matched and guard < 120:
        guard += 1
        rows = _list_rows(d)
        if not rows:
            raise MessagesUIError("Messages list unavailable during polling — no scrolling attempted")
        if seeking:
            # Exact text plus key: never attach a saved retry to a different
            # sender/day merely because its amount and HH:MM:SS happen to agree.
            target = next((r for r in rows if r["key"] == start_row["key"]
                           and r["sig"] == start_row["sig"]), None)
            if target:
                # Seeking may already have seen a clipped version of this row.
                # Discard that seek-only history so neither it nor rows below it
                # are accidentally considered processed when the full row appears.
                seen = {r["key"] for r in rows[:rows.index(target)]}
                if start_row.get("after"):
                    seen.add(target["key"])
                seeking = False
                log(f"retrying unfinished scan at {target['key']} (verified rows above skipped)")
            else:
                seen.update(r["key"] for r in rows)
        nextrow = next((r for r in rows if r["key"] not in seen), None)
        if nextrow is None:                       # nothing new on screen
            if scrolls >= scroll_limit:
                break
            d.swipe_ext("up", scale=0.7)
            time.sleep(1.2)
            scrolls += 1
            scrolled_rows = _list_rows(d)
            if not scrolled_rows:
                raise MessagesUIError("Messages list unavailable after scrolling")
            if all(r["key"] in seen for r in scrolled_rows):
                reached_bottom = True
                break                             # reached the bottom
            continue
        seen.add(nextrow["key"])
        if not nextrow["incoming"]:               # skip outgoing — don't open it
            continue
        read_result = _read_verified_detail(d, nextrow, log=log)
        if not read_result:
            # Do not scan past an unreadable incoming row and then advance the
            # watermark to a newer verified row. That would strand this transaction
            # behind the watermark forever. Verified rows above it may still send;
            # global ref dedup makes their retry safe while the old watermark is held.
            blocked = True
            failed_row = {"key": nextrow["key"], "sig": nextrow["sig"]}
            log(f"⚠ holding watermark at {last_ref or '(unset)'} — incoming row {nextrow['key']} was not verified")
            break
        rec, verified_row_sig = read_result
        if not detail_incoming(rec):               # confirm money received
            continue
        # reached a transaction we already sent last time -> STOP (do NOT include it)
        if last_ref is not None and rec["ref"] == last_ref:
            matched = True
            log(f"stop reference reached = {last_ref}")
            break
        # already collected this ref in THIS poll (e.g. re-read after a scroll) ->
        # skip so it can never be sent twice
        if rec["ref"] in done_refs:
            continue
        done_refs.add(rec["ref"])
        last_verified_row = {"key": nextrow["key"], "sig": nextrow["sig"], "after": True}
        # Source the sender from the SAME verified detail (label-anchored), so the
        # name can't come from a different/stale list-row snapshot and get attached
        # to the wrong transaction. Fall back to the list-row text only if the
        # detail didn't yield a counterparty.
        fa, fn = detail_source(rec)
        if not (fa or fn):
            fa, fn = row_source(verified_row_sig)
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

    if last_ref is not None and not matched and not blocked:
        # A deliberately invalid/manual watermark is used as a one-time override:
        # verified candidates continue to the gateway, and a successful send moves
        # the boundary to the newest real reference. The caller's send-success gate
        # still prevents advancement on transient gateway failures.
        log(f"⚠ watermark {last_ref} was not found — continuing with {len(new)} verified candidate(s)")

    pending = None
    if failed_row:
        pending = {"last_ref": last_ref, "start_row": failed_row}
    elif seeking:
        pending = {"last_ref": last_ref, "start_row": start_row}
        log("⚠ pending detail row not visible within scan limit — retained for retry")
    elif start_row and not matched and not reached_bottom:
        # A long unfinished range can take several cycles. Resume AFTER the last
        # verified row instead of opening that same range from its beginning.
        pending = {"last_ref": last_ref, "start_row": last_verified_row or start_row}

    return {"first_run": last_ref is None,
            "last_ref": last_ref if blocked else (new_top or last_ref),
            "scan_ref": new_top or last_ref,
            "pending_scan": pending,
            "new": new}


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
