import uiautomator2 as u2
import os, sys, time, json, csv, re

# device serial from argv or the SERIAL env var (no hardcoded device)
DEVICE = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("SERIAL", "")
if not DEVICE:
    sys.exit("usage: python scrape_messages.py <serial>")
MAX_SCROLLS = 8          # how many times to scroll for more rows
d = u2.connect(DEVICE)

# Lao field labels -> english keys (best-effort)
LABELS = {
    "ຫາບັນຊີ": "to_account",
    "ລາຍລະອຽດ": "details",
    "ເລກໃບບິນ": "bill_no",
    "ເງິນອອກ": "amount_out",
    "ເງິນເຂົ້າ": "amount_in",
    "ຈຳນວນເງິນ": "amount",
}

def open_messages():
    d.app_start("com.bcel.bcelone")
    time.sleep(2)
    try:
        d(text='ຂໍ້ຄວາມ').click()
        time.sleep(1.5)
    except Exception:
        pass

def row_bounds():
    """Full-width clickable rows above the bottom tab bar (y<2100)."""
    rows = []
    for el in d.xpath('//*[@clickable="true"]').all():
        b = el.attrib.get("bounds", "")
        m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", b)
        if not m:
            continue
        x1, y1, x2, y2 = map(int, m.groups())
        if x1 == 0 and x2 >= 1000 and y2 < 2100 and (y2 - y1) > 150:
            rows.append((y1, y2))
    return sorted(rows)

def extract_detail():
    """Read all text nodes on the message detail screen into a dict."""
    texts = []
    for el in d.xpath('//*').all():
        t = (el.text or "").strip()
        rid = (el.attrib.get("resource-id") or "").split("/")[-1]
        if t and rid not in ("tab_text", "clock", "battery_percentage_view"):
            texts.append((rid, t))

    rec = {"type": "", "account": "", "time": "", "kind": "", "raw": []}
    pending_label = None
    for rid, t in texts:
        if rid == "titletext":
            rec["type"] = t
        elif rid == "source":
            rec["account"] = t
        elif rid == "time":
            rec["time"] = t
        elif rid == "subtitlehead":
            rec["kind"] = t
        elif rid == "titleprev":      # "Close" button
            continue
        else:
            rec["raw"].append(t)
            if t in LABELS:
                pending_label = LABELS[t]
            elif pending_label:
                rec[pending_label] = t
                pending_label = None
    return rec

def go_back():
    try:
        d(text="Close").click()
    except Exception:
        d.press("back")
    time.sleep(0.8)

# ---- main loop ----
open_messages()
results, seen = [], set()

for s in range(MAX_SCROLLS + 1):
    rows = row_bounds()
    if not rows:
        break
    for (y1, y2) in rows:
        cy = (y1 + y2) / 2 / 2408          # normalized y for current 1080x2408 screen
        d.click(0.5, cy)
        time.sleep(1.2)
        if "titletext" not in d.dump_hierarchy():  # didn't open a detail
            continue
        rec = extract_detail()
        go_back()
        key = (rec.get("bill_no"), rec.get("time"), rec.get("type"))
        if key in seen:
            continue
        seen.add(key)
        results.append(rec)
        print(f"  [{len(results)}] {rec['type']} | {rec.get('time')} | {rec.get('bill_no','')}")
    # scroll for more
    d.swipe_ext("up", scale=0.8)
    time.sleep(1.2)

# ---- save ----
with open("messages.json", "w", encoding="utf-8") as f:
    json.dump(results, f, ensure_ascii=False, indent=2)

# flat CSV of common keys
keys = ["type", "kind", "account", "time", "to_account", "details", "amount_out", "amount_in", "amount", "bill_no"]
with open("messages.csv", "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
    w.writeheader()
    for r in results:
        w.writerow(r)

print(f"\nDone. Extracted {len(results)} messages -> messages.json / messages.csv")
