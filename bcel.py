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


def replace_focused_text(d, value, log=print, field_name="input", clear_chars=32):
    raw_value = str(value or "")
    try:
        d.send_keys("", clear=True)
    except Exception as e:
        log(f"{field_name}: retry clear")
        try:
            d.shell("input keyevent 123 " + ("67 " * clear_chars))
            time.sleep(0.15)
        except Exception:
            pass
    d.send_keys(raw_value, clear=False)


def type_into_security_answer(d, value, log=print, field_name="security answer", clear_chars=32):
    raw_value = str(value or "")
    try:
        d.shell("input keyevent 123 " + ("67 " * clear_chars))
        time.sleep(0.15)
    except Exception:
        pass
    d.send_keys(raw_value, clear=False)


def type_into_active_field(d, value, log=print, field_name="active input", clear_chars=32):
    raw_value = str(value or "")
    try:
        d.shell("input keyevent 123 " + ("67 " * clear_chars))
        time.sleep(0.15)
    except Exception:
        pass
    d.send_keys(raw_value, clear=False)


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


def _only_digits(value):
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def _norm_text(value):
    return re.sub(r"\s+", "", str(value or "")).lower()


def _center(bounds):
    x1, y1, x2, y2 = bounds
    return (x1 + x2) // 2, (y1 + y2) // 2


def _masked_value_matches(saved_value, displayed_text, min_prefix=1, min_suffix=1):
    saved = _only_digits(saved_value)
    if not saved:
        return False
    shown = _norm_text(displayed_text)
    prefix = []
    i = 0
    while i < len(shown):
        ch = shown[i]
        if ch == "x":
            break
        if ch.isdigit():
            prefix.append(ch)
        i += 1
    suffix = []
    j = len(shown) - 1
    while j >= 0:
        ch = shown[j]
        if ch == "x":
            break
        if ch.isdigit():
            suffix.append(ch)
        j -= 1
    prefix = "".join(prefix)
    suffix = "".join(reversed(suffix))
    if prefix and len(prefix) < min_prefix:
        return False
    if suffix and len(suffix) < min_suffix:
        return False
    if prefix and not saved.startswith(prefix):
        return False
    if suffix and not saved.endswith(suffix):
        return False
    if not prefix and not suffix:
        shown_digits = _only_digits(displayed_text)
        return bool(shown_digits) and shown_digits == saved
    return True


def _masked_visible_parts(displayed_text):
    shown = _norm_text(displayed_text)
    prefix = []
    i = 0
    while i < len(shown):
        ch = shown[i]
        if ch == "x":
            break
        if ch.isdigit():
            prefix.append(ch)
        i += 1
    suffix = []
    j = len(shown) - 1
    while j >= 0:
        ch = shown[j]
        if ch == "x":
            break
        if ch.isdigit():
            suffix.append(ch)
        j -= 1
    return "".join(prefix), "".join(reversed(suffix))


def _masked_account_matches(saved_account, displayed_text):
    saved = _only_digits(saved_account)
    if len(saved) < 8:
        return False
    prefix, suffix = _masked_visible_parts(displayed_text)
    if not prefix and not suffix:
        return False
    if prefix and not saved.startswith(prefix):
        return False
    if suffix and not saved.endswith(suffix):
        return False
    return True


def _normalize_security_label(value):
    return _norm_text(value)


def _collect_hierarchy_nodes(d):
    root = ET.fromstring(d.dump_hierarchy())
    nodes = []
    for el in root.iter():
        bounds = _parse_bounds(el.attrib.get("bounds", ""))
        text = (el.attrib.get("text") or "").strip()
        hint = (el.attrib.get("hint") or "").strip()
        desc = (el.attrib.get("content-desc") or "").strip()
        rid = (el.attrib.get("resource-id") or "").strip()
        clazz = (el.attrib.get("class") or "").strip()
        combined = _norm_text(" ".join(part for part in (text, hint, desc, rid) if part))
        nodes.append({
            "bounds": bounds,
            "text": text,
            "hint": hint,
            "desc": desc,
            "rid": rid,
            "class": clazz,
            "combined": combined,
        })
    nodes.sort(key=lambda n: (n["bounds"][1] if n["bounds"] else 10**9,
                              n["bounds"][0] if n["bounds"] else 10**9))
    return nodes


def _extract_center_modal_message(nodes):
    texts = []
    for node in nodes:
        bounds = node.get("bounds")
        text = (node.get("text") or "").strip()
        rid = (node.get("rid") or "").split("/")[-1]
        if not bounds or not text:
            continue
        x1, y1, x2, y2 = bounds
        width = x2 - x1
        height = y2 - y1
        if width < 180:
            continue
        if y1 < 120 or y2 > 1280:
            continue
        if rid in {"titletext", "titleback", "next", "navigation"}:
            continue
        if text in {"ຕົກລົງ", "OK", "Close", "ປິດ", "Transfer", "ໂອນເງິນ"}:
            continue
        texts.append((y1, text))
    if not texts:
        return ""
    lines = []
    seen = set()
    for _, text in sorted(texts, key=lambda item: item[0]):
        if text in seen:
            continue
        seen.add(text)
        lines.append(text)
    return " ".join(lines[:4]).strip()


def _find_text_node(nodes, labels, contains=False):
    wanted = tuple(_norm_text(label) for label in labels if label)
    for node in nodes:
        text = (node.get("text") or "").strip()
        if not text:
            continue
        current = _norm_text(text)
        if contains:
            if any(label in current for label in wanted):
                return node
        elif current in wanted:
            return node
    return None


def _is_transfer_success_page(nodes):
    success_title = _find_text_node(
        nodes,
        ("ໂອນເງິນສໍາເລັດ", "ໂອນເງິນສຳເລັດ", "transfer success", "successful transfer"),
        contains=True,
    )
    done_button = _find_text_node(
        nodes,
        ("ສໍາເລັດ", "ສຳເລັດ", "ສໍາເລັດແລ້ວ", "ສຳເລັດແລ້ວ", "done"),
        contains=True,
    )
    receipt_anchor = _find_text_node(
        nodes,
        ("ເລກ Ticket", "ticket", "ຈໍານວນເງິນ", "withdraw from merchant balance"),
        contains=True,
    )
    return bool(done_button and (success_title or receipt_anchor))


def _security_answer_field_specs(answer_value):
    specs = []
    for idx in (1, 2, 3):
        label = str(idx)
        if isinstance(answer_value, dict):
            answer = str(answer_value.get(f"withdraw_a{idx}", "") or "").strip()
        else:
            answer = str(answer_value or "").strip() if idx == 1 else ""
        specs.append({
            "index": idx,
            "answer": answer,
            "labels": (
                f"ຄໍາຕອບທີ {label}",
                f"ຄໍາຕອບທີ{label}",
                f"ຄຳຕອບທີ {label}",
                f"ຄຳຕອບທີ{label}",
                f"ຄໍາຖາມທີ {label}",
                f"ຄໍາຖາມທີ{label}",
                f"ຄຳຖາມທີ {label}",
                f"ຄຳຖາມທີ{label}",
                f"Question {label}",
                f"question {label}",
                f"Answer {label}",
                f"answer {label}",
            ),
            "answer_field_ids": (
                f"ans{label}",
            ),
            "page_ids": (
                f"pageq{label}",
            ),
        })
    return specs


def _security_label_matches(node, label_norms):
    if not node.get("combined"):
        return False
    combined = node["combined"]
    text = _normalize_security_label(node.get("text", ""))
    hint = _normalize_security_label(node.get("hint", ""))
    desc = _normalize_security_label(node.get("desc", ""))
    rid = _normalize_security_label(node.get("rid", ""))
    if any(label in combined for label in label_norms):
        return True
    if any(label in text for label in label_norms):
        return True
    if any(label in hint for label in label_norms):
        return True
    if any(label in desc for label in label_norms):
        return True
    if any(label in rid for label in label_norms):
        return True
    return False


def _security_page_title_matches(nodes):
    title_tokens = (
        "ຢັ້ງຢືນຕົວຕົນເພີ່ມເຕີມ",
        "additional identity verification",
        "identity verification",
    )
    title_norms = tuple(_norm_text(t) for t in title_tokens)
    for node in nodes:
        rid = (node.get("rid") or "").split("/")[-1]
        if rid != "titletext":
            continue
        text = _norm_text(node.get("text", ""))
        if any(tok in text for tok in title_norms):
            return True
    return False


def _is_security_answer_page(nodes):
    if _security_page_title_matches(nodes):
        return True
    for node in nodes:
        rid = (node.get("rid") or "").split("/")[-1]
        if rid in {"pageq1", "pageq2", "pageq3", "ans1", "ans2", "ans3"}:
            return True
    return False


def _detect_security_question_index(nodes, specs):
    for spec in specs:
        for node in nodes:
            rid = (node.get("rid") or "").split("/")[-1]
            if rid in set(spec.get("answer_field_ids", ())):
                return spec["index"]
            if rid in set(spec.get("page_ids", ())):
                return spec["index"]
            if _security_label_matches(node, tuple(_norm_text(label) for label in spec["labels"])):
                return spec["index"]
    return None


def _is_transfer_description_page(nodes):
    title_tokens = (
        "ຄໍາອະທິບາຍການໂອນ",
        "ຄຳອະທິບາຍການໂອນ",
        "transfer description",
    )
    field_tokens = (
        "ປ້ອນຄໍາອະທິບາຍ",
        "ປ້ອນຄຳອະທິບາຍ",
        "input transfer description",
        "description",
    )
    title_norms = tuple(_norm_text(t) for t in title_tokens)
    field_norms = tuple(_norm_text(t) for t in field_tokens)
    title_found = False
    field_found = False
    for node in nodes:
        combined = node.get("combined", "")
        rid = (node.get("rid") or "").split("/")[-1]
        if combined and any(tok in combined for tok in title_norms):
            title_found = True
        if combined and any(tok in combined for tok in field_norms):
            field_found = True
        if rid in {"desc", "description", "transferdescription", "remark", "note"}:
            field_found = True
    return title_found or field_found


def _account_match_details(saved_account, displayed_text):
    saved = _only_digits(saved_account)
    prefix, suffix = _masked_visible_parts(displayed_text)
    return {
        "screen": displayed_text,
        "screen_prefix": prefix,
        "screen_suffix": suffix,
        "saved_prefix_ok": bool(prefix) and saved.startswith(prefix),
        "saved_suffix_ok": bool(suffix) and saved.endswith(suffix),
        "matched": _masked_account_matches(saved_account, displayed_text),
    }


def _masked_card_matches(saved_card_no, displayed_text):
    saved = _only_digits(saved_card_no)
    shown_digits = _only_digits(displayed_text)
    if len(saved) < 10 or len(shown_digits) < 10:
        return False
    return _masked_value_matches(saved_card_no, displayed_text, min_prefix=8, min_suffix=2)


def _suffix_matches(saved_card_no, displayed_text, min_visible=2):
    saved = _only_digits(saved_card_no)
    shown = _only_digits(displayed_text)
    if len(saved) < min_visible or len(shown) < min_visible:
        return False
    max_len = min(4, len(shown), len(saved))
    for size in range(max_len, min_visible - 1, -1):
        if shown.endswith(saved[-size:]):
            return True
    return False


def _canonical_name(value):
    text = re.sub(r"\s+", " ", str(value or "").strip()).upper()
    parts = [p for p in re.split(r"[\s/]+", text) if p]
    honorifics = {"MR", "MRS", "MS", "MISS", "MISTER"}
    parts = [p for p in parts if p.rstrip(".") not in honorifics]
    return " ".join(parts)


def _section_anchor(nodes, tokens):
    norm_tokens = tuple(_norm_text(t) for t in tokens)
    for node in nodes:
        text = node.get("text", "")
        if not text:
            continue
        normalized = _norm_text(text)
        if any(tok in normalized for tok in norm_tokens):
            return node
    return None


def _masked_account_below_anchor(nodes, anchor, saved_account, min_prefix=5, min_suffix=3):
    if not anchor:
        return None
    ax1, ay1, ax2, ay2 = anchor["bounds"]
    candidates = []
    for node in nodes:
        text = node.get("text", "")
        bounds = node.get("bounds")
        if not text or not bounds:
            continue
        x1, y1, x2, y2 = bounds
        if y1 < ay1 - 40 or y1 > ay2 + 320:
            continue
        if x2 < ax1 - 80:
            continue
        if "x" not in _norm_text(text):
            continue
        if not _masked_account_matches(saved_account, text):
            continue
        candidates.append(node)
    if not candidates:
        return None
    return sorted(candidates, key=lambda n: n["bounds"][1])[0]


def _name_near_account(nodes, account_node, expected_name):
    wanted = _canonical_name(expected_name)
    if not wanted or not account_node:
        return None
    nx1, ny1, nx2, ny2 = account_node["bounds"]
    candidates = []
    for node in nodes:
        text = (node.get("text") or "").strip()
        bounds = node.get("bounds")
        if not text or not bounds:
            continue
        x1, y1, x2, y2 = bounds
        if y2 < ny1 - 160 or y1 > ny2 + 60:
            continue
        if x2 < nx1 - 60:
            continue
        canonical = _canonical_name(text)
        if not canonical:
            continue
        if wanted in canonical or canonical in wanted:
            candidates.append(node)
    if not candidates:
        return None
    return sorted(candidates, key=lambda n: abs(n["bounds"][1] - ny1))[0]


def verify_unionpay_card_detail(d, card_no, log=print, timeout=8):
    saved = _only_digits(card_no)
    if len(saved) < 10:
        raise RuntimeError("saved withdraw card number is missing or too short")

    deadline = time.time() + timeout
    last_dump = ""
    while time.time() < deadline:
        try:
            last_dump = d.dump_hierarchy()
            root = ET.fromstring(last_dump)
        except Exception:
            time.sleep(0.5)
            continue

        texts = [(el.attrib.get("text") or "").strip() for el in root.iter()]
        texts = [t for t in texts if t]
        unionpay_seen = any("unionpay" in _norm_text(t) for t in texts)
        prefix_seen = False
        suffix_seen = False
        matched_text = ""
        for text in texts:
            digits = _only_digits(text)
            if len(digits) < 10:
                continue
            if digits[:8] == saved[:8]:
                prefix_seen = True
                if _suffix_matches(saved, digits):
                    suffix_seen = True
                    matched_text = text
                    break
        if unionpay_seen and prefix_seen and suffix_seen:
            log(f"verified UnionPay detail card: saved={saved[:8]}...{saved[-4:]} screen={matched_text}")
            return True
        time.sleep(0.5)

    raise RuntimeError(f"card detail verification failed for saved card ending {saved[-4:]}")


def verify_transfer_money_page(d, log=print, timeout=8):
    title_tokens = (
        "ການໂອນເງິນ",
        "transfer money",
    )
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            root = ET.fromstring(d.dump_hierarchy())
        except Exception:
            time.sleep(0.5)
            continue
        texts = [(el.attrib.get("text") or "").strip() for el in root.iter()]
        texts = [t for t in texts if t]
        norm = [_norm_text(t) for t in texts]
        if any(any(tok in t for tok in title_tokens) for t in norm):
            log("verified transfer money page")
            return True
        time.sleep(0.5)
    raise RuntimeError("transfer money page verification failed")


def _is_transfer_money_page_from_dump(d):
    try:
        root = ET.fromstring(d.dump_hierarchy())
    except Exception:
        return False
    texts = [(el.attrib.get("text") or "").strip() for el in root.iter()]
    texts = [t for t in texts if t]
    norm = [_norm_text(t) for t in texts]
    return any("ການໂອນເງິນ" in t or "transfermoney" in t for t in norm)


def select_source_account_on_transfer_page(d, account_no, log=print):
    saved = _only_digits(account_no)
    if len(saved) < 8:
        raise RuntimeError("saved withdraw account number is missing or too short")

    try:
        root = ET.fromstring(d.dump_hierarchy())
    except Exception as e:
        raise RuntimeError(f"could not read transfer money page: {e}")

    nodes = []
    for el in root.iter():
        text = (el.attrib.get("text") or "").strip()
        bounds = _parse_bounds(el.attrib.get("bounds", ""))
        if text and bounds:
            nodes.append({"text": text, "bounds": bounds})

    candidates = []
    for node in nodes:
        text = node["text"]
        if not any(ch.isdigit() for ch in text):
            continue
        if "x" not in _norm_text(text):
            continue
        if _masked_account_matches(saved, text):
            candidates.append(node)

    if not candidates:
        raise RuntimeError(f"transfer source account mismatch for saved account ending {saved[-4:]}")

    number = sorted(candidates, key=lambda n: n["bounds"][1])[0]
    nx1, ny1, nx2, ny2 = number["bounds"]
    related = [number]
    for node in nodes:
        text = node["text"]
        if not text:
            continue
        x1, y1, x2, y2 = node["bounds"]
        if y1 < ny1 - 140 or y2 > ny2 + 160:
            continue
        if x2 < nx1 - 220 or x1 > nx2 + 260:
            continue
        related.append(node)
    row_bounds = (
        min(n["bounds"][0] for n in related),
        min(n["bounds"][1] for n in related),
        max(n["bounds"][2] for n in related),
        max(n["bounds"][3] for n in related),
    )
    cx, cy = _center(row_bounds)
    screen_prefix, screen_suffix = _masked_visible_parts(number["text"])
    log(
        f"matched transfer source account: "
        f"screen_prefix={screen_prefix or '-'} screen_suffix={screen_suffix or '-'} "
        f"saved_prefix_match={'yes' if saved.startswith(screen_prefix) else 'no'} "
        f"saved_suffix_match={'yes' if saved.endswith(screen_suffix) else 'no'} "
        f"screen={number['text']}"
    )
    d.click(cx, cy)
    time.sleep(1)
    return True


def verify_receiver_account_page(d, log=print, timeout=8):
    title_tokens = (
        "ບັນຊີປາຍທາງ",
        "receivers account",
        "receiver account",
    )
    add_tokens = (
        "ເພີ່ມບັນຊີປາຍທາງ",
        "add receivers account",
        "receivers account",
    )
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            root = ET.fromstring(d.dump_hierarchy())
        except Exception:
            time.sleep(0.5)
            continue
        texts = [(el.attrib.get("text") or "").strip() for el in root.iter()]
        norm = [_norm_text(t) for t in texts if t]
        title_ok = any(any(tok in t for tok in title_tokens) for t in norm)
        add_ok = any(any(tok in t for tok in add_tokens) for t in norm)
        if title_ok and add_ok:
            log("verified receiver account page")
            return True
        time.sleep(0.5)
    raise RuntimeError("receiver account page verification failed")


def input_receiver_account(d, to_account, log=print, timeout=8):
    raw_value = str(to_account or "").strip()
    if len(raw_value) < 6:
        raise RuntimeError("toAccount from request is missing or too short")

    candidates = (
        "ເລກບັນຊີ / ເລກບັດ / ເບີໂທ",
        "Account / Card / Phone",
        "account / card / phone",
        "account number / card number / phone",
        "receivers account",
        "receiver account",
        "account number",
        "card number",
        "phone number",
    )
    candidate_norms = tuple(_norm_text(label) for label in candidates)
    last_seen = []
    deadline = time.time() + timeout
    while time.time() < deadline:
        for label in candidates:
            try:
                el = d(text=label)
                if el.exists:
                    el.click()
                    time.sleep(0.4)
                    replace_focused_text(d, raw_value, log=log, field_name="receiver account")
                    log(f"entered receiver account from request: ...{raw_value[-4:]}")
                    return True
            except Exception:
                pass
            try:
                xp = d.xpath(f'//*[@text="{label}" or @hint="{label}"]')
                if xp.exists:
                    xp.click()
                    time.sleep(0.4)
                    replace_focused_text(d, raw_value, log=log, field_name="receiver account")
                    log(f"entered receiver account from request: ...{raw_value[-4:]}")
                    return True
            except Exception:
                pass
        try:
            root = ET.fromstring(d.dump_hierarchy())
            nodes = []
            for el in root.iter():
                bounds = _parse_bounds(el.attrib.get("bounds", ""))
                if not bounds:
                    continue
                text = (el.attrib.get("text") or "").strip()
                hint = (el.attrib.get("hint") or "").strip()
                desc = (el.attrib.get("content-desc") or "").strip()
                rid = (el.attrib.get("resource-id") or "").strip()
                clazz = (el.attrib.get("class") or "").strip()
                combined = _norm_text(" ".join(part for part in (text, hint, desc, rid) if part))
                if combined:
                    last_seen.append(combined[:80])
                    last_seen = last_seen[-6:]
                nodes.append({
                    "bounds": bounds,
                    "text": text,
                    "hint": hint,
                    "desc": desc,
                    "rid": rid,
                    "class": clazz,
                    "combined": combined,
                })

            for node in nodes:
                combined = node["combined"]
                if not combined:
                    continue
                if not any(label in combined for label in candidate_norms):
                    continue
                cx, cy = _center(node["bounds"])
                log(f"focusing receiver account field: {node['text'] or node['hint'] or node['desc'] or node['rid']}")
                d.click(cx, cy)
                time.sleep(0.4)
                replace_focused_text(d, raw_value, log=log, field_name="receiver account")
                log(f"entered receiver account from request: ...{raw_value[-4:]}")
                return True

            for node in nodes:
                combined = node["combined"]
                clazz = _norm_text(node["class"])
                if not combined:
                    continue
                english_like = "account" in combined and "card" in combined and "phone" in combined
                lao_like = "ເລກບັນຊີ" in combined and "ເລກບັດ" in combined and "ເບີໂທ" in combined
                input_like = "edittext" in clazz or "textfield" in clazz or "input" in combined
                if not ((english_like or lao_like) and input_like):
                    continue
                cx, cy = _center(node["bounds"])
                log(f"focusing receiver account input by hierarchy: {node['text'] or node['hint'] or node['desc'] or node['rid']}")
                d.click(cx, cy)
                time.sleep(0.4)
                replace_focused_text(d, raw_value, log=log, field_name="receiver account")
                log(f"entered receiver account from request: ...{raw_value[-4:]}")
                return True
        except Exception:
            pass
        time.sleep(0.5)
    seen = ", ".join(last_seen[-3:]) if last_seen else "no visible input labels"
    raise RuntimeError(f"receiver account input not found (seen: {seen})")


def click_receiver_next(d, log=print, timeout=8):
    labels = (
        "ຕໍ່ໄປ",
        "Next",
        "ເພີ່ມບັນຊີ",
        "Add Account",
    )
    deadline = time.time() + timeout
    while time.time() < deadline:
        for label in labels:
            try:
                el = d(text=label)
                if el.exists:
                    info = el.info
                    bounds = info.get("bounds") or {}
                    if bounds:
                        cx = int((bounds.get("left", 0) + bounds.get("right", 0)) / 2)
                        cy = int((bounds.get("top", 0) + bounds.get("bottom", 0)) / 2)
                        log(f"clicking receiver next: {label}")
                        d.click(cx, cy)
                        time.sleep(1)
                        return True
                    el.click()
                    log(f"clicking receiver next: {label}")
                    time.sleep(1)
                    return True
            except Exception:
                pass
        time.sleep(0.5)
    raise RuntimeError("receiver next button not found (tried: ຕໍ່ໄປ, Next, ເພີ່ມບັນຊີ, Add Account)")


def verify_transfer_amount_page(d, from_account, to_account, to_name, log=print, timeout=8):
    from_saved = _only_digits(from_account)
    to_saved = _only_digits(to_account)
    if len(from_saved) < 8:
        raise RuntimeError("saved withdraw source account is missing or too short")
    if len(to_saved) < 8:
        raise RuntimeError("request toAccount is missing or too short")

    from_tokens = ("ຈາກບັນຊີ", "from account", "form account")
    to_tokens = ("ຫາບັນຊີ", "to account")
    last_state = {}
    failure_reason = ""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            root = ET.fromstring(d.dump_hierarchy())
        except Exception:
            time.sleep(0.5)
            continue

        nodes = []
        texts = []
        for el in root.iter():
            text = (el.attrib.get("text") or "").strip()
            bounds = _parse_bounds(el.attrib.get("bounds", ""))
            if text:
                texts.append(text)
            if text and bounds:
                nodes.append({"text": text, "bounds": bounds})

        from_anchor = _section_anchor(nodes, from_tokens)
        to_anchor = _section_anchor(nodes, to_tokens)
        from_node = _masked_account_below_anchor(nodes, from_anchor, from_saved, min_prefix=5, min_suffix=3)
        to_node = _masked_account_below_anchor(nodes, to_anchor, to_saved, min_prefix=5, min_suffix=3)
        name_node = _name_near_account(nodes, to_node, to_name) if to_node else None

        state = {
            "title_seen": texts[:8],
            "from_anchor": from_anchor["text"] if from_anchor else "",
            "from_match": from_node["text"] if from_node else "",
            "from_screen_prefix": _masked_visible_parts(from_node["text"])[0] if from_node else "",
            "from_screen_suffix": _masked_visible_parts(from_node["text"])[1] if from_node else "",
            "to_anchor": to_anchor["text"] if to_anchor else "",
            "to_match": to_node["text"] if to_node else "",
            "to_screen_prefix": _masked_visible_parts(to_node["text"])[0] if to_node else "",
            "to_screen_suffix": _masked_visible_parts(to_node["text"])[1] if to_node else "",
            "name_match": name_node["text"] if name_node else "",
            "name_expected": _canonical_name(to_name),
        }
        last_state = state

        if not from_node or not to_node:
            if not from_anchor:
                failure_reason = "from account section label not found"
            elif not from_node:
                failure_reason = "from account masked value did not match saved account"
            elif not to_anchor:
                failure_reason = "to account section label not found"
            else:
                failure_reason = "to account masked value did not match request account"
            time.sleep(0.5)
            continue

        if not name_node:
            expected = _canonical_name(to_name)
            screen = to_node["text"] if to_node else "-"
            raise RuntimeError(
                f"transfer amount page receiver name mismatch for {expected or '-'} "
                f"(to_account_screen={screen})"
            )

        log(
            f"verified transfer amount page: "
            f"from={from_node['text']} to={to_node['text']} name={name_node['text']}"
        )
        return True

    detail = (
        f"reason={failure_reason or '-'} "
        f"from_anchor={last_state.get('from_anchor') or '-'} "
        f"from_match={last_state.get('from_match') or '-'} "
        f"to_anchor={last_state.get('to_anchor') or '-'} "
        f"to_match={last_state.get('to_match') or '-'} "
        f"name_match={last_state.get('name_match') or '-'} "
        f"name_expect={last_state.get('name_expected') or '-'}"
    )
    raise RuntimeError(f"transfer amount page verification failed ({detail})")


def verify_transfer_confirmation_page(d, from_account, to_account, to_name, log=print, timeout=8):
    from_saved = _only_digits(from_account)
    to_saved = _only_digits(to_account)
    if len(from_saved) < 8:
        raise RuntimeError("saved withdraw source account is missing or too short")
    if len(to_saved) < 8:
        raise RuntimeError("request toAccount is missing or too short")

    from_tokens = ("ຈາກບັນຊີ", "from account", "form account")
    to_tokens = ("ຫາບັນຊີ", "to account")
    last_state = {}
    failure_reason = ""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            root = ET.fromstring(d.dump_hierarchy())
        except Exception:
            time.sleep(0.5)
            continue

        nodes = []
        texts = []
        for el in root.iter():
            text = (el.attrib.get("text") or "").strip()
            bounds = _parse_bounds(el.attrib.get("bounds", ""))
            if text:
                texts.append(text)
            if text and bounds:
                nodes.append({"text": text, "bounds": bounds})

        from_anchor = _section_anchor(nodes, from_tokens)
        to_anchor = _section_anchor(nodes, to_tokens)
        from_node = _masked_account_below_anchor(nodes, from_anchor, from_saved, min_prefix=5, min_suffix=3)
        to_node = _masked_account_below_anchor(nodes, to_anchor, to_saved, min_prefix=5, min_suffix=3)
        name_node = _name_near_account(nodes, to_node, to_name) if to_node else None

        state = {
            "title_seen": texts[:8],
            "from_anchor": from_anchor["text"] if from_anchor else "",
            "from_match": from_node["text"] if from_node else "",
            "to_anchor": to_anchor["text"] if to_anchor else "",
            "to_match": to_node["text"] if to_node else "",
            "name_match": name_node["text"] if name_node else "",
            "name_expected": _canonical_name(to_name),
        }
        last_state = state

        if not from_node or not to_node:
            if not from_anchor:
                failure_reason = "from account section label not found"
            elif not from_node:
                failure_reason = "from account masked value did not match saved account"
            elif not to_anchor:
                failure_reason = "to account section label not found"
            else:
                failure_reason = "to account masked value did not match request account"
            time.sleep(0.5)
            continue

        if not name_node:
            expected = _canonical_name(to_name)
            screen = to_node["text"] if to_node else "-"
            raise RuntimeError(
                f"transfer confirmation receiver name mismatch for {expected or '-'} "
                f"(to_account_screen={screen})"
            )

        log("confirm page verified")
        return True

    detail = (
        f"reason={failure_reason or '-'} "
        f"from_anchor={last_state.get('from_anchor') or '-'} "
        f"from_match={last_state.get('from_match') or '-'} "
        f"to_anchor={last_state.get('to_anchor') or '-'} "
        f"to_match={last_state.get('to_match') or '-'} "
        f"name_match={last_state.get('name_match') or '-'} "
        f"name_expect={last_state.get('name_expected') or '-'}"
    )
    raise RuntimeError(f"transfer confirmation page verification failed ({detail})")


def click_transfer_confirm(d, log=print, timeout=8):
    labels = ("ໂອນເງິນ", "Transfer")
    deadline = time.time() + timeout
    while time.time() < deadline:
        for label in labels:
            try:
                el = d(text=label)
                if el.exists:
                    info = el.info
                    bounds = info.get("bounds") or {}
                    if bounds:
                        cx = int((bounds.get("left", 0) + bounds.get("right", 0)) / 2)
                        cy = int((bounds.get("top", 0) + bounds.get("bottom", 0)) / 2)
                        log(f"click transfer: {label}")
                        d.click(cx, cy)
                        time.sleep(1)
                        return True
                    el.click()
                    log(f"click transfer: {label}")
                    time.sleep(1)
                    return True
            except Exception:
                pass
        time.sleep(0.5)
    raise RuntimeError("transfer confirm button not found")


def check_transfer_result_modal(d, log=print, timeout=6):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            nodes = _collect_hierarchy_nodes(d)
        except Exception:
            time.sleep(0.5)
            continue

        if _is_transfer_success_page(nodes):
            done_node = _find_text_node(
                nodes,
                ("ສໍາເລັດ", "ສຳເລັດ", "ສໍາເລັດແລ້ວ", "ສຳເລັດແລ້ວ", "Done"),
                contains=True,
            )
            bounds = done_node.get("bounds") if done_node else None
            if bounds:
                log("transfer success")
                d.click(*_center(bounds))
                time.sleep(1.2)
                if go_home(d, log=log):
                    log("current page: home")
                    return True
                log("current page: not home")
                return True

        message = _extract_center_modal_message(nodes)
        button_found = False
        for node in nodes:
            text = (node.get("text") or "").strip()
            if text in {"ຕົກລົງ", "OK", "Close", "ປິດ"}:
                button_found = True
                break

        if message and button_found:
            log(f"transfer failed: {message}")
            raise RuntimeError(f"transfer failed: {message}")

        time.sleep(0.5)
    return False


def recover_to_bcel_home(d, log=print, tries=3):
    for _ in range(tries):
        try:
            nodes = _collect_hierarchy_nodes(d)
        except Exception:
            nodes = []

        clicked = False
        for node in nodes:
            text = (node.get("text") or "").strip()
            bounds = node.get("bounds")
            if text not in {"ຕົກລົງ", "OK", "Close", "ປິດ"} or not bounds:
                continue
            log(f"close popup: {text}")
            d.click(*_center(bounds))
            time.sleep(0.8)
            clicked = True
            break

        if clicked:
            continue

        if by_pass_popup_network_failure(d):
            log("close popup")
            continue

        break

    ok = go_home(d, log=log)
    if ok:
        log("back to home")
    else:
        log("back to home failed")
    return ok


def input_transfer_amount(d, amount, log=print, timeout=8):
    raw_value = str(amount if amount is not None else "").strip()
    if not raw_value:
        raise RuntimeError("withdraw amount is missing")

    candidates = (
        "ຈໍານວນເງິນທີ່ໂອນ",
        "Transfer amount",
        "transfer amount",
    )
    label_candidates = (
        "ກະລຸນາປ້ອນຈໍານວນ",
        "Please enter amount",
        "please enter amount",
        "ຈໍານວນເງິນທີ່ໂອນ",
        "Transfer amount",
        "transfer amount",
    )
    candidate_norms = tuple(_norm_text(label) for label in candidates)
    label_norms = tuple(_norm_text(label) for label in label_candidates)
    last_seen = []
    deadline = time.time() + timeout
    direct_attempted = False
    while time.time() < deadline:
        if not direct_attempted:
            direct_attempted = True
            try:
                time.sleep(0.3)
                replace_focused_text(d, raw_value, log=log, field_name="transfer amount")
                log(f"entered transfer amount: {raw_value}")
                return True
            except Exception:
                pass
        for label in candidates:
            try:
                xp = d.xpath(f'//*[@text="{label}" or @hint="{label}"]')
                if xp.exists:
                    xp.click()
                    time.sleep(0.4)
                    replace_focused_text(d, raw_value, log=log, field_name="transfer amount")
                    log(f"entered transfer amount: {raw_value}")
                    return True
            except Exception:
                pass
            try:
                el = d(text=label)
                if el.exists:
                    el.click()
                    time.sleep(0.4)
                    replace_focused_text(d, raw_value, log=log, field_name="transfer amount")
                    log(f"entered transfer amount: {raw_value}")
                    return True
            except Exception:
                pass
        try:
            root = ET.fromstring(d.dump_hierarchy())
            nodes = []
            for el in root.iter():
                bounds = _parse_bounds(el.attrib.get("bounds", ""))
                if not bounds:
                    continue
                text = (el.attrib.get("text") or "").strip()
                hint = (el.attrib.get("hint") or "").strip()
                desc = (el.attrib.get("content-desc") or "").strip()
                rid = (el.attrib.get("resource-id") or "").strip()
                clazz = (el.attrib.get("class") or "").strip()
                combined = _norm_text(" ".join(part for part in (text, hint, desc, rid) if part))
                if combined:
                    last_seen.append(combined[:80])
                    last_seen = last_seen[-8:]
                node = {
                    "bounds": bounds,
                    "text": text,
                    "hint": hint,
                    "desc": desc,
                    "rid": rid,
                    "class": clazz,
                    "combined": combined,
                }
                nodes.append(node)
                if not any(label in combined for label in candidate_norms):
                    continue
                cx, cy = _center(bounds)
                log(f"focusing transfer amount field: {text or hint or desc or rid or clazz}")
                d.click(cx, cy)
                time.sleep(0.4)
                replace_focused_text(d, raw_value, log=log, field_name="transfer amount")
                log(f"entered transfer amount: {raw_value}")
                return True

            for node in nodes:
                combined = node["combined"]
                if not combined:
                    continue
                if not any(label in combined for label in label_norms):
                    continue
                lx1, ly1, lx2, ly2 = node["bounds"]
                field_candidates = []
                for target in nodes:
                    tx1, ty1, tx2, ty2 = target["bounds"]
                    t_combined = target["combined"]
                    t_class = _norm_text(target["class"])
                    width = tx2 - tx1
                    height = ty2 - ty1
                    if ty1 < ly2 - 10 or ty1 > ly2 + 260:
                        continue
                    if tx1 > lx2 + 120 or tx2 < lx1 - 120:
                        continue
                    input_like = (
                        "edittext" in t_class or
                        "textfield" in t_class or
                        "input" in t_combined or
                        width > 500
                    )
                    if not input_like:
                        continue
                    if height < 60 or height > 240:
                        continue
                    field_candidates.append(target)
                if field_candidates:
                    best = sorted(
                        field_candidates,
                        key=lambda t: (t["bounds"][1], -(t["bounds"][2] - t["bounds"][0]))
                    )[0]
                    cx, cy = _center(best["bounds"])
                    log(
                        "focusing transfer amount field below label: "
                        f"label={node['text'] or node['hint'] or node['desc'] or node['rid']} "
                        f"target={best['text'] or best['hint'] or best['desc'] or best['rid'] or best['class']}"
                    )
                    d.click(cx, cy)
                    time.sleep(0.4)
                    replace_focused_text(d, raw_value, log=log, field_name="transfer amount")
                    log(f"entered transfer amount: {raw_value}")
                    return True
        except Exception:
            pass
        time.sleep(0.5)
    seen = ", ".join(last_seen[-4:]) if last_seen else "no visible amount labels"
    raise RuntimeError(f"transfer amount input not found (seen: {seen})")


def input_security_answer(d, answer, log=print, timeout=8):
    field_specs = _security_answer_field_specs(answer)
    active_specs = [spec for spec in field_specs if spec["answer"]]
    if not active_specs:
        raise RuntimeError("security answer 1 is missing in device credentials")

    candidate_norms = tuple(_norm_text(label) for spec in active_specs for label in spec["labels"])
    last_seen = []
    filled_indexes = set()
    flow_deadline = time.time() + max(timeout, 8) * 4

    while time.time() < flow_deadline:
        try:
            nodes = _collect_hierarchy_nodes(d)
        except Exception:
            time.sleep(0.5)
            continue

        if not nodes:
            time.sleep(0.5)
            continue

        for node in nodes:
            combined = node.get("combined", "")
            if combined:
                last_seen.append(combined[:80])
                last_seen = last_seen[-12:]

        if not _is_security_answer_page(nodes):
            if filled_indexes:
                log("security answers done")
                return True
            return False

        current_index = _detect_security_question_index(nodes, active_specs)
        if current_index is None:
            time.sleep(0.5)
            continue

        spec = next((item for item in active_specs if item["index"] == current_index), None)
        if not spec:
            raise RuntimeError(f"security answer {current_index} is required but missing in device credentials")
        answer_value = spec["answer"]
        spec_norms = tuple(_norm_text(label) for label in spec["labels"])
        spec_nodes = [node for node in nodes if _security_label_matches(node, spec_norms)]
        if spec_nodes:
            chosen_label = spec_nodes[0]
            label_text = chosen_label.get("text") or chosen_label.get("hint") or chosen_label.get("desc") or chosen_label.get("rid") or "-"
        else:
            chosen_label = None
            label_text = f"answer {spec['index']}"

        log(f"security question {current_index}")

        try:
            type_into_security_answer(d, answer_value, log=log, field_name=f"security answer {current_index}")
            log(f"answer {current_index} entered")
            click_receiver_next(d, log=log)
            step_deadline = time.time() + timeout
            while time.time() < step_deadline:
                try:
                    next_nodes = _collect_hierarchy_nodes(d)
                except Exception:
                    time.sleep(0.5)
                    continue
                next_index = _detect_security_question_index(next_nodes, active_specs)
                if not _is_security_answer_page(next_nodes):
                    log("security answers done")
                    return True
                if next_index != current_index:
                    filled_indexes.add(current_index)
                    break
                time.sleep(0.5)
            if current_index in filled_indexes:
                continue
            log(f"security question {current_index}: retry input")
        except Exception as e:
            log(f"security question {current_index}: retry input")

        field_candidates = []
        spec_field_ids = set(spec.get("answer_field_ids", ()))
        for target in nodes:
            target_rid = (target.get("rid") or "").split("/")[-1]
            if target_rid in spec_field_ids:
                field_candidates.append(target)
        if chosen_label and chosen_label.get("bounds"):
            lx1, ly1, lx2, ly2 = chosen_label["bounds"]
            for target in nodes:
                if target is chosen_label or not target.get("bounds"):
                    continue
                tx1, ty1, tx2, ty2 = target["bounds"]
                width = tx2 - tx1
                height = ty2 - ty1
                if ty1 < ly2 - 10 or ty1 > ly2 + 260:
                    continue
                if tx1 > lx2 + 140 or tx2 < lx1 - 140:
                    continue
                tclass = _normalize_security_label(target.get("class", ""))
                tcombined = target.get("combined", "")
                input_like = (
                    "edittext" in tclass or
                    "textfield" in tclass or
                    "input" in tcombined or
                    width > 250
                )
                if not input_like:
                    continue
                if height < 50 or height > 260:
                    continue
                field_candidates.append(target)

        if not field_candidates:
            seen = ", ".join(last_seen[-4:]) if last_seen else "no visible security question labels"
            raise RuntimeError(f"security answer {current_index} input not found (seen: {seen})")

        best = sorted(
            field_candidates,
            key=lambda t: (
                0 if (t.get("rid") or "").split("/")[-1] in spec_field_ids else 1,
                t["bounds"][1] if t.get("bounds") else 10**9,
                -((t["bounds"][2] - t["bounds"][0]) if t.get("bounds") else 0),
            )
        )[0]
        cx, cy = _center(best["bounds"])
        d.click(cx, cy)
        time.sleep(0.8)
        type_into_security_answer(d, answer_value, log=log, field_name=f"security answer {current_index}")
        filled_indexes.add(current_index)
        log(f"answer {current_index} entered")
        click_receiver_next(d, log=log)

        step_deadline = time.time() + timeout
        while time.time() < step_deadline:
            try:
                next_nodes = _collect_hierarchy_nodes(d)
            except Exception:
                time.sleep(0.5)
                continue
            next_index = _detect_security_question_index(next_nodes, active_specs)
            if not _is_security_answer_page(next_nodes):
                log("security answers done")
                return True
            if next_index != current_index:
                break
            time.sleep(0.5)

    seen = ", ".join(last_seen[-4:]) if last_seen else "no visible security question labels"
    raise RuntimeError(f"security answer flow did not finish (seen: {seen})")


def input_transfer_description(d, remark, log=print, timeout=8):
    raw_value = str(remark or "").strip()
    if not raw_value:
        return False

    deadline = time.time() + timeout
    last_seen = []
    while time.time() < deadline:
        try:
            nodes = _collect_hierarchy_nodes(d)
        except Exception:
            time.sleep(0.5)
            continue

        if not nodes:
            time.sleep(0.5)
            continue

        if not _is_transfer_description_page(nodes):
            return False

        log("transfer description")

        # On this screen the cursor is often already active in the top input box.
        # Try typing into the active field first before doing any element hunting.
        try:
            type_into_active_field(d, raw_value, log=log, field_name="transfer description")
            log("description entered")
            click_receiver_next(d, log=log)
            return True
        except Exception:
            log("transfer description: retry input")

        label_norms = tuple(_norm_text(t) for t in (
            "ຄໍາອະທິບາຍການໂອນ",
            "ຄຳອະທິບາຍການໂອນ",
            "transfer description",
            "ປ້ອນຄໍາອະທິບາຍ",
            "ປ້ອນຄຳອະທິບາຍ",
        ))

        anchor = None
        for node in nodes:
            combined = node.get("combined", "")
            if combined:
                last_seen.append(combined[:80])
                last_seen = last_seen[-10:]
            if anchor is None and combined and any(tok in combined for tok in label_norms):
                anchor = node

        field_candidates = []
        for node in nodes:
            rid = (node.get("rid") or "").split("/")[-1]
            clazz = _norm_text(node.get("class", ""))
            combined = node.get("combined", "")
            bounds = node.get("bounds")
            if not bounds:
                continue
            x1, y1, x2, y2 = bounds
            width = x2 - x1
            height = y2 - y1
            input_like = (
                rid in {"desc", "description", "transferdescription", "remark", "note"} or
                "edittext" in clazz or
                "textfield" in clazz or
                "input" in combined or
                width > 420
            )
            if not input_like:
                continue
            if width < 300 or height < 50 or height > 320:
                continue
            if anchor and anchor.get("bounds"):
                ax1, ay1, ax2, ay2 = anchor["bounds"]
                if y1 < ay1 - 40 or y1 > ay2 + 320:
                    continue
            field_candidates.append(node)

        if not field_candidates:
            seen = ", ".join(last_seen[-4:]) if last_seen else "no visible description labels"
            raise RuntimeError(f"transfer description input not found (seen: {seen})")

        best = sorted(
            field_candidates,
            key=lambda t: (
                0 if (t.get("rid") or "").split("/")[-1] in {"desc", "description", "transferdescription", "remark", "note"} else 1,
                t["bounds"][1],
                -(t["bounds"][2] - t["bounds"][0]),
            )
        )[0]
        cx, cy = _center(best["bounds"])
        d.click(cx, cy)
        time.sleep(0.8)
        type_into_active_field(d, raw_value, log=log, field_name="transfer description")
        log("description entered")
        click_receiver_next(d, log=log)
        return True

    seen = ", ".join(last_seen[-4:]) if last_seen else "no visible description labels"
    raise RuntimeError(f"transfer description flow did not finish (seen: {seen})")


def click_transfer_on_unionpay_detail(d, log=print, timeout=8):
    targets = ("ໂອນເງິນ", "Transfer")
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            nodes = _collect_hierarchy_nodes(d)
        except Exception:
            time.sleep(0.5)
            continue

        candidates = []
        for node in nodes:
            text = (node.get("text") or "").strip()
            bounds = node.get("bounds")
            if text not in targets or not bounds:
                continue
            x1, y1, x2, y2 = bounds
            width = x2 - x1
            height = y2 - y1
            if width < 40 or height < 20:
                continue
            candidates.append(node)

        if not candidates:
            time.sleep(0.5)
            continue

        candidates.sort(key=lambda n: (n["bounds"][1], n["bounds"][0]))
        target_node = candidates[-1]
        tx1, ty1, tx2, ty2 = target_node["bounds"]
        cx = int((tx1 + tx2) / 2)
        cy = int((ty1 + ty2) / 2)
        text_h = max(ty2 - ty1, 1)

        for tap_y, label in (
            (cy, "clicking transfer text"),
            (max(ty1 - int(text_h * 1.2), 0), "clicking slightly above transfer text"),
        ):
            log(f"{label}: {target_node['text']}")
            d.click(cx, tap_y)
            time.sleep(1.0)
            if _is_transfer_money_page_from_dump(d):
                return True

        time.sleep(0.5)

    raise RuntimeError("transfer menu 'ໂອນເງິນ' / 'Transfer' not found on UnionPay detail page")


def select_unionpay_card(d, card_no, log=print):
    saved = _only_digits(card_no)
    if len(saved) < 10:
        log("withdraw card select skipped — no saved card number")
        return False

    go_home(d, log=log)
    if d(text="ບັດ").exists:
        d(text="ບັດ").click()
        time.sleep(1)

    try:
        root = ET.fromstring(d.dump_hierarchy())
    except Exception as e:
        raise RuntimeError(f"could not read card list: {e}")

    nodes = []
    for el in root.iter():
        text = (el.attrib.get("text") or "").strip()
        bounds = _parse_bounds(el.attrib.get("bounds", ""))
        if text and bounds:
            nodes.append({"text": text, "bounds": bounds})

    titles = [n for n in nodes if "unionpay" in _norm_text(n["text"])]
    if not titles:
        raise RuntimeError("UnionPay card row not found on screen")

    for title in titles:
        tx1, ty1, tx2, ty2 = title["bounds"]
        candidates = []
        for node in nodes:
            if node is title:
                continue
            text = node["text"]
            if len(_only_digits(text)) < 10:
                continue
            nx1, ny1, nx2, ny2 = node["bounds"]
            if ny1 < ty1 - 20 or ny1 > ty2 + 220:
                continue
            if nx2 < tx1 - 40:
                continue
            if _masked_card_matches(saved, text):
                candidates.append(node)
        if not candidates:
            continue
        number = sorted(candidates, key=lambda n: n["bounds"][1])[0]
        nx1, ny1, nx2, ny2 = number["bounds"]
        log(f"matched UnionPay card: saved={saved[:8]}...{saved[-4:]} screen={number['text']}")
        cx, cy = _center(number["bounds"])
        d.click(cx, cy)
        time.sleep(0.8)
        try:
            verify_unionpay_card_detail(d, saved, log=log, timeout=2.5)
        except Exception:
            tcx, tcy = _center(title["bounds"])
            log("retry card tap on unionpay text")
            d.click(tcx, tcy)
            time.sleep(0.8)
        verify_unionpay_card_detail(d, saved, log=log)
        click_transfer_on_unionpay_detail(d, log=log)
        return True

    raise RuntimeError(f"UnionPay card number mismatch for saved card ending {saved[-4:]}")


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
    cur = d.app_current()

    if fresh:
        d.app_start(PKG, stop=True)
        time.sleep(1.5)
    elif cur.get("package") == PKG:
        by_pass_popup_network_failure(d)
        if at_login(d):
            do_login(d, password, username, log=log)
            return d
        if is_home(d) or d(text="My QR").exists:
            log("reuse current BCEL home")
            return d
        log(f"resume BCEL from {cur.get('activity')}")
        if go_home(d, log=log):
            return d
        d.app_start(PKG, stop=False)
        time.sleep(1.5)
    else:
        d.app_start(PKG, stop=False)
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
