"""
Silent concurrent runner: launches automate.py on many devices at once,
each in its own process, with no console noise (per-device log files).

Config: devices.txt — one device per line:
    <serial> | <amount> | <description> | <password>
Example:
    <ip:port> | 1250000 | Invoice 555 | <password>
    <ip:port> | 500000  | Order 77    | <password>
Password is optional (only used if the app needs to log in again).
Lines starting with # are ignored.

Run:
    ./venv/bin/python run_batch.py              # uses devices.txt
    ./venv/bin/python run_batch.py myfile.txt   # custom config
    SUBMIT=1 ./venv/bin/python run_batch.py      # also tap "Create QR"

Logs go to ./logs/<serial>.log . The script waits for all to finish and
prints a one-line summary per device.
"""
import os
import sys
import subprocess
import concurrent.futures

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
LOG_DIR = os.path.join(HERE, "logs")
os.makedirs(LOG_DIR, exist_ok=True)


def parse_config(path):
    """Fields are pipe-separated: serial | amount | description | password.
    Falls back to whitespace-splitting if no '|' is present (legacy format)."""
    jobs = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "|" in line:
                parts = [p.strip() for p in line.split("|")]
            else:
                parts = line.split(None, 2)
            serial = parts[0]
            amount = parts[1] if len(parts) > 1 else ""
            desc = parts[2] if len(parts) > 2 else ""
            pwd = parts[3] if len(parts) > 3 else ""
            jobs.append((serial, amount, desc, pwd))
    return jobs


def run_one(job):
    serial, amount, desc, pwd = job
    log_path = os.path.join(LOG_DIR, serial.replace(":", "_").replace(".", "-") + ".log")
    # pass params positionally: serial amount description password
    cmd = [PY, os.path.join(HERE, "automate.py"), serial,
           amount or "200000", desc or "", pwd or ""]
    with open(log_path, "w") as logf:
        proc = subprocess.run(cmd, cwd=HERE, stdout=logf,
                              stderr=subprocess.STDOUT, env=os.environ)
    return serial, proc.returncode, log_path


def main():
    cfg = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "devices.txt")
    if not os.path.exists(cfg):
        print(f"config not found: {cfg}")
        sys.exit(1)
    jobs = parse_config(cfg)
    if not jobs:
        print("no devices in config")
        sys.exit(1)

    print(f"Running {len(jobs)} device(s) concurrently…  (logs in ./logs/)")
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        for serial, code, log_path in pool.map(run_one, jobs):
            status = "OK" if code == 0 else f"FAILED (exit {code})"
            print(f"  {serial:28} {status:18} -> {os.path.relpath(log_path, HERE)}")
    print("All done.")


if __name__ == "__main__":
    main()
