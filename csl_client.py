"""
Client for the CSL Payment Gateway (csl-payment-gateways).

Implements the auth signature from docs/authentication.md and the
POST /bcel/setup and POST /bcel/transactions calls from docs/bcel.md.
"""
import hashlib
import requests

DEFAULT_API_URL = "https://paymentgateway.108pay.co"


def _js_string(value) -> str:
    """Match JavaScript template-string conversion used by Utils.genHash."""
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, (dict,)):
        return "[object Object]"
    if isinstance(value, (list, tuple)):
        return ",".join(_js_string(item) for item in value)
    return str(value)


def gen_hash(payload: dict, api_key: str) -> str:
    """Reproduce Utils.genHash(payload, apiKey):
      1. sort keys alphabetically, case-insensitive
      2. join 'key=value' pairs with the separator '&api-key=<apiKey>' placed
         *between* pairs (so a single-key body has no separator)
      3. MD5 -> hex -> UPPERCASE
    """
    keys = sorted(payload.keys(), key=lambda k: k.lower())
    parts = [f"{k}={_js_string(payload[k])}" for k in keys]
    sep = f"&api-key={api_key}"
    hash_string = sep.join(parts)
    return hashlib.md5(hash_string.encode("utf-8")).hexdigest().upper()


def _url(api_url: str, path: str) -> str:
    return (api_url or DEFAULT_API_URL).rstrip("/") + path


def _request_error_message(error, api_url: str) -> str:
    if isinstance(error, requests.exceptions.ConnectionError):
        return f"Cannot connect to sync service at {(api_url or DEFAULT_API_URL).rstrip('/')}. Please start it and try again."
    if isinstance(error, requests.exceptions.Timeout):
        return "Sync service timed out. Please try again."
    return str(error)


def setup_webhook(api_url: str, client_id: str, api_key: str, webhook: str,
                  timeout: int = 15):
    """POST /bcel/setup — register the client's webhook URL.
    Returns (ok: bool, message: str)."""
    body = {"webhook": webhook}
    sig = gen_hash(body, api_key)
    url = _url(api_url, "/bcel/setup")
    headers = {
        "Content-Type": "application/json",
        "client-id": client_id,
        "hash-signature": sig,
    }
    try:
        r = requests.post(url, json=body, headers=headers, timeout=timeout)
    except requests.exceptions.RequestException as e:
        return False, _request_error_message(e, api_url)
    try:
        data = r.json()
    except Exception:
        data = {"raw": r.text[:200]}
    ok = r.status_code == 200 and (data.get("status") is True)
    if ok:
        return True, "Webhook registered (status: true)"
    code = data.get("code", r.status_code)
    msg = data.get("message", r.text[:120])
    return False, f"setup failed [{code}]: {msg}"


def post_transactions(api_url: str, client_id: str, api_key: str, transactions,
                      timeout: int = 15):
    """POST /bcel/transactions with the detected transaction payload(s).
    Returns (ok: bool, message: str, transient: bool) — transient=True means the
    gateway couldn't be reached (network/timeout/5xx), so the caller should retry
    rather than advance its watermark and drop the transactions."""
    body = {"transactions": transactions}
    sig = gen_hash(body, api_key)
    url = _url(api_url, "/bcel/transactions")
    headers = {
        "Content-Type": "application/json",
        "client-id": client_id,
        "hash-signature": sig,
    }
    try:
        r = requests.post(url, json=body, headers=headers, timeout=timeout)
    except requests.exceptions.RequestException as e:
        return False, _request_error_message(e, api_url), True   # couldn't reach -> retry
    try:
        data = r.json()
    except Exception:
        data = {"raw": r.text[:200]}
    ok = r.status_code == 200 and (data.get("status") is True)
    if ok:
        return True, "Transactions posted (status: true)", False
    code = data.get("code", r.status_code)
    msg = data.get("message", r.text[:120])
    transient = r.status_code >= 500            # server error -> retry; 4xx -> reject
    return False, f"transactions failed [{code}]: {msg}", transient


if __name__ == "__main__":
    # sanity check against the doc example (single-key body, key not in hash)
    h = gen_hash({"webhook": "https://client.example.com/bcel/callback"}, "SECRET")
    print("hash:", h, "(should be MD5 of 'webhook=https://client.example.com/bcel/callback' uppercased)")
    import hashlib as _h
    expect = _h.md5(b"webhook=https://client.example.com/bcel/callback").hexdigest().upper()
    print("match:", h == expect)
