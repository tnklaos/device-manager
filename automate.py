"""CLI wrapper around bcel.create_qr().

Run:
  python automate.py <serial> [amount] [description] [password]
Examples:
  python automate.py <serial> 200000 "Order 444" <password>

Params can also come from env vars: SERIAL, AMOUNT, DESCRIPTION, PASSWORD.
SUBMIT=1 also submits + confirms and extracts the QR string.
"""
import os
import sys
import bcel

serial = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("SERIAL", "R8YY40Y3W4L")
amount = sys.argv[2] if len(sys.argv) > 2 else os.environ.get("AMOUNT", "200000")
description = sys.argv[3] if len(sys.argv) > 3 else os.environ.get("DESCRIPTION", "444")
password = sys.argv[4] if len(sys.argv) > 4 else os.environ.get("PASSWORD", "Pk1234")
username = sys.argv[5] if len(sys.argv) > 5 else os.environ.get("USERNAME", "")
submit = os.environ.get("SUBMIT", "0") == "1"

if not serial:
    sys.exit("usage: python automate.py <serial> [amount] [description] [password]")



def log(msg):
    print(f"[{serial}] {msg}", flush=True)


res = bcel.create_qr(serial, amount, description, password, username=username,
                     submit=submit, log=log)


# if submit and res["qr_string"]:
#     tag = bcel.tag_for(serial)
#     with open(f"qr_{tag}.txt", "w") as f:
#         f.write(res["qr_string"])
#     log(f"done -> {os.path.basename(res['screenshot'])} , qr_{tag}.txt")
# else:
#     log(f"done -> {os.path.basename(res['screenshot'])}")
