"""Unsigned transaction delivery for user-configured callback sets."""

import requests


def normalize_transaction(transaction):
    """Return the stable public payload accepted by custom callbacks."""
    return {
        "serial": transaction.get("serial", ""),
        "type": transaction.get("type", ""),
        "kind": transaction.get("kind", ""),
        "from_account": transaction.get("from_account", ""),
        "from_name": transaction.get("from_name", ""),
        "to_account": transaction.get("to_account") or transaction.get("account") or "",
        "details": transaction.get("details", ""),
        "ref": transaction.get("ref") or transaction.get("bill_no") or "",
        "amount_in": transaction.get("amount_in") or transaction.get("amount") or "",
        "time": transaction.get("time", ""),
    }


def post_transactions(callback_url, header, api_key, transactions, timeout=15):
    """Post normalized transactions and classify failures for watermark handling."""
    body = {"transactions": [normalize_transaction(t) for t in transactions]}
    headers = {"Content-Type": "application/json", header: api_key}
    try:
        response = requests.post(
            callback_url,
            json=body,
            headers=headers,
            timeout=timeout,
        )
    except requests.exceptions.Timeout:
        return False, "Custom callback timed out. Please try again.", True
    except requests.exceptions.RequestException as error:
        return False, f"Cannot connect to custom callback: {error}", True

    if 200 <= response.status_code < 300:
        return True, f"Custom callback accepted [{response.status_code}]", False

    message = response.text[:200]
    transient = response.status_code >= 500
    return (
        False,
        f"Custom callback failed [{response.status_code}]: {message}",
        transient,
    )
