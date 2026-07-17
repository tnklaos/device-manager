# Custom Forwarding Set Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add assignable custom callback sets that forward normalized transaction batches with one configured API-key header and no CSL signing or save-time verification.

**Architecture:** Keep gateway and custom profiles in the existing `settings["sets"]` collection with a type discriminator. A focused custom HTTP client owns payload normalization and response classification; `Engine._send` dispatches by set type and retains the shared deduplication, persistence, logging, and watermark contract.

**Tech Stack:** Python 3, requests, Flask, unittest/mock, Electron renderer JavaScript/HTML/CSS.

## Global Constraints

- Legacy set records without `type` behave as `gateway`.
- Custom API keys are never returned by an API response or written to logs.
- Custom saves make no outbound request.
- Custom delivery sends no `client-id`, `hash-signature`, or MD5 signature.
- HTTP 2xx succeeds, network/timeout/5xx retries, and 4xx is permanent.
- Existing CSL gateway behavior and payload remain unchanged.

---

### Task 1: Custom Callback HTTP Client

**Files:**
- Create: `custom_client.py`
- Create: `tests/test_custom_client.py`

**Interfaces:**
- Consumes: verified transaction dictionaries from `Engine._send`.
- Produces: `normalize_transaction(transaction) -> dict` and `post_transactions(callback_url, header, api_key, transactions, timeout=15) -> (ok, message, transient)`.

- [ ] **Step 1: Write failing client tests**

Tests mock `requests.post` and assert the body contains only `serial`, `type`, `kind`, `from_account`, `from_name`, `to_account`, `details`, `ref`, `amount_in`, and `time`; headers contain only JSON content type plus the configured header; 2xx succeeds; 4xx is permanent; 5xx and request exceptions are transient.

- [ ] **Step 2: Verify RED**

Run: `./venv/bin/python -m unittest tests/test_custom_client.py -v`

Expected: import failure because `custom_client.py` does not exist.

- [ ] **Step 3: Implement the client**

```python
def normalize_transaction(t):
    from_account, from_name = source fields already attached by Engine
    return {
        "serial": t.get("serial", ""),
        "type": t.get("type", ""),
        "kind": t.get("kind", ""),
        "from_account": from_account,
        "from_name": from_name,
        "to_account": t.get("account") or t.get("to_account") or "",
        "details": t.get("details", ""),
        "ref": t.get("ref") or t.get("bill_no") or "",
        "amount_in": t.get("amount_in") or t.get("amount") or "",
        "time": t.get("time", ""),
    }
```

Post `{"transactions": normalized}` with `{header: api_key, "Content-Type": "application/json"}`. Return the agreed result tuple without parsing a required response schema.

- [ ] **Step 4: Verify GREEN**

Run: `./venv/bin/python -m unittest tests/test_custom_client.py -v`

Expected: all custom-client tests pass.

---

### Task 2: Set Persistence, Validation, API, and Send Dispatch

**Files:**
- Modify: `engine.py:15,300-385,675-770`
- Modify: `server.py:72-83`
- Create: `tests/test_custom_sets.py`

**Interfaces:**
- Consumes: `custom_client.post_transactions(...)` from Task 1.
- Produces: type-aware `Engine.sets()`, `Engine.save_set(...)`, `Engine.setup_set_webhook(...)`, and `_send` dispatch.

- [ ] **Step 1: Write failing engine/API tests**

Create isolated temporary settings/transaction files. Assert legacy sets return `type="gateway"`; custom fields are returned with `has_secret` but not `api_key`; blank secret preserves the prior value; invalid header/URL/missing fields raise `ValueError`; custom webhook registration makes no CSL call; `_send` calls `custom_client` rather than `csl_client` and passes source/to-account fields.

- [ ] **Step 2: Verify RED**

Run: `./venv/bin/python -m unittest tests/test_custom_sets.py -v`

Expected: failures because set typing and custom dispatch are absent.

- [ ] **Step 3: Implement type-aware sets**

Extend `save_set` with `set_type="gateway"`, `header=""`, and `callback_url=""`. Validate custom headers with `^[!#$%&'*+.^_`|~0-9A-Za-z-]+$`, validate callback URLs with `urllib.parse.urlparse` and schemes `http`/`https`, preserve a blank saved API key, and save only fields relevant to the selected type.

Update `server.py` to pass `type`, `header`, and `callback_url`, returning HTTP 400 with `{ok:false,message}` for `ValueError`.

- [ ] **Step 4: Implement delivery dispatch**

Import `custom_client`. For custom profiles validate callback credentials, call the custom client with verified transactions carrying `serial`, `from_account`, and `from_name`, then use the existing common success/dedup/logging code. Gateway profiles continue through `csl_client` unchanged.

- [ ] **Step 5: Verify GREEN**

Run: `./venv/bin/python -m unittest tests/test_custom_sets.py -v`

Expected: all set and dispatch tests pass.

---

### Task 3: Custom Set Renderer

**Files:**
- Modify: `electron/renderer/index.html:78-105`
- Modify: `electron/renderer/app.js:185-290`
- Modify: `electron/renderer/style.css`
- Create: `tests/test_custom_set_ui.py`

**Interfaces:**
- Consumes: type-aware `/api/sets` GET/POST responses from Task 2.
- Produces: `+ Custom Set`, type-specific editor fields, and custom save flow without `/webhook`.

- [ ] **Step 1: Write failing renderer contract tests**

Read the renderer files and assert `new-custom`, `＋ Custom Set`, `set-header`, `set-api-key`, and `set-callback` exist; assert the custom save branch posts type `custom` and does not call the webhook endpoint; assert gateway save still performs verification.

- [ ] **Step 2: Verify RED**

Run: `./venv/bin/python -m unittest tests/test_custom_set_ui.py -v`

Expected: failures because the custom UI contract is absent.

- [ ] **Step 3: Implement the editor**

Add separate gateway/custom field groups. `new` creates gateway profiles; `new-custom` creates custom profiles. Existing set tabs use their stored type. Hide Register Webhook for custom sets. Custom save sends `type`, `name`, `header`, `secret_key`, and `callback_url`, displays `Saved ✓`, reloads sets, and never invokes `/webhook`.

- [ ] **Step 4: Verify GREEN and full regression suite**

Run: `./venv/bin/python -m unittest tests/test_custom_set_ui.py -v`

Run: `./venv/bin/python -m unittest discover -s tests -v`

Expected: all tests pass with zero failures.

- [ ] **Step 5: Verify the running UI**

Restart dev, open Settings, create a custom set with a local test callback, confirm the API key is redacted after reload, assign it in a device dropdown, and confirm saving did not call CSL `/bcel/setup`. Use a mocked/local callback for delivery; do not send a real bank transaction during UI verification.

- [ ] **Step 6: Review final changes**

Run: `git diff --check && git status --short`

Expected: no whitespace errors and only the planned client, engine/API, renderer, tests, and documentation changes.
