"""
Connect an Android device over WiFi using the phone's
"Pair device with QR code" screen (Settings > Developer options >
Wireless debugging > Pair device with QR code).

How it works:
  1. We invent a pairing service name + password.
  2. We render a QR encoding  WIFI:T:ADB;S:<name>;P:<password>;;
  3. You scan it with the phone's QR pairing screen.
  4. The phone broadcasts an mDNS _adb-tls-pairing._tcp service named <name>.
  5. We discover it via `adb mdns services`, run `adb pair`, then `adb connect`.

CLI:   ./venv/bin/python qr_connect.py
"""
import secrets
import string
import subprocess
import time
import re

PAIR_TYPE = "_adb-tls-pairing._tcp"
CONNECT_TYPE = "_adb-tls-connect._tcp"


def adb(*args, timeout=15):
    return subprocess.run(["adb", *args], capture_output=True, text=True,
                          timeout=timeout).stdout


def make_credentials():
    name = "ADB_WIFI_" + secrets.token_hex(3)
    alphabet = string.ascii_letters + string.digits
    password = "".join(secrets.choice(alphabet) for _ in range(10))
    return name, password


def qr_payload(name, password):
    # Exact format the Android wireless-debugging QR scanner expects.
    return f"WIFI:T:ADB;S:{name};P:{password};;"


def find_service(name, service_type):
    """Look through `adb mdns services` for a service instance == name."""
    out = adb("mdns", "services")
    for line in out.splitlines():
        if service_type in line and name in line:
            m = re.search(r"(\d+\.\d+\.\d+\.\d+):(\d+)", line)
            if m:
                return f"{m.group(1)}:{m.group(2)}"
    return None


def wait_and_pair(name, password, on_status=print, timeout=90):
    """Poll mDNS until the phone advertises the pairing service, then pair+connect.
    on_status(msg) is a callback so a GUI can show progress.
    Returns the connected serial (ip:port) or raises RuntimeError.
    """
    adb("start-server")
    on_status("Waiting for you to scan the QR code on the phone…")

    deadline = time.time() + timeout
    pair_addr = None
    while time.time() < deadline:
        pair_addr = find_service(name, PAIR_TYPE)
        if pair_addr:
            break
        time.sleep(1)
    if not pair_addr:
        raise RuntimeError("Timed out waiting for the phone to scan the QR code.")

    on_status(f"Phone detected at {pair_addr} — pairing…")
    out = adb("pair", pair_addr, password)
    if "Successfully paired" not in out:
        raise RuntimeError(f"Pairing failed: {out.strip()}")
    on_status("Paired ✓  — now connecting…")

    # After pairing, find the connect service (different port) and connect.
    connect_addr = None
    deadline = time.time() + 20
    while time.time() < deadline:
        connect_addr = find_service(name, CONNECT_TYPE) or find_service("", CONNECT_TYPE)
        if connect_addr:
            break
        time.sleep(1)
    if not connect_addr:
        raise RuntimeError("Paired, but couldn't find the connect service. "
                           "Try `adb connect <ip:port>` manually.")

    out = adb("connect", connect_addr)
    on_status(out.strip())
    if "connected" not in out.lower():
        raise RuntimeError(f"Connect failed: {out.strip()}")
    return connect_addr


def print_qr_terminal(payload):
    import qrcode
    qr = qrcode.QRCode(border=2)
    qr.add_data(payload)
    qr.make()
    qr.print_ascii(invert=True)


if __name__ == "__main__":
    name, password = make_credentials()
    payload = qr_payload(name, password)
    print("\nOn the phone open:  Settings > Developer options > "
          "Wireless debugging > Pair device with QR code\n")
    print("Then scan this QR code:\n")
    print_qr_terminal(payload)
    print(f"\n(name={name}  password={password})\n")
    try:
        serial = wait_and_pair(name, password)
        print(f"\n✅ Connected: {serial}")
    except Exception as e:
        print(f"\n❌ {e}")
