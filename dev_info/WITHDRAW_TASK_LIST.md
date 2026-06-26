# Withdraw Technical Task List

Temporary implementation checklist for adding withdraw support to this project.
This document translates the withdraw plan/spec into concrete code tasks by file.

## Current Agreed Model

Use these rules for implementation:

1. Persist a per-device role in device credentials: `deposit` or `withdraw`.
2. Do not allow a combined `both` role.
3. Do not expect `device_serial` from backoffice withdraw requests.
4. Maintain one active withdraw device at a time in app state.
5. Route pending withdraw jobs to that active withdraw device.

## Development Status (2026-06-25)

Completed:

1. Added `deposit` / `withdraw` role persistence to device credentials
2. Added role selection to the Electron credentials modal
3. Added withdraw request endpoint shell in the backend
4. Added logging for withdraw request payload and current device activity
5. Added active withdraw device start placeholder
6. Added app/login prepare step for active withdraw device on request receive
7. Added request rejection when no withdraw device is active or when prepare is already in progress

Next recommended tasks:

1. Auto-start ngrok when a withdraw device is started
2. Persist withdraw requests in SQLite
3. Enforce duplicate protection using `withdrawalId`
4. Add explicit withdraw runner state instead of reusing `monitoring`
5. Implement post-login navigation to the withdraw screen
6. Implement actual withdraw form fill and submit
7. Add callback sender for success/fail response back to backoffice

## Goal

Implement an async withdraw workflow in this codebase without interfering with the existing incoming transaction monitoring path.

## Existing Project Boundaries

From [AGENTS.md](/Users/csl-dev/Desktop/csl/device-manager/AGENTS.md):

- Electron uses [server.py](/Users/csl-dev/Desktop/csl/device-manager/server.py) over HTTP/SSE
- business logic belongs in [engine.py](/Users/csl-dev/Desktop/csl/device-manager/engine.py)
- mobile automation belongs in [bcel.py](/Users/csl-dev/Desktop/csl/device-manager/bcel.py)
- gateway/client HTTP helpers belong in [csl_client.py](/Users/csl-dev/Desktop/csl/device-manager/csl_client.py)
- current incoming transaction flow is money-critical and should not be broken

## Phase 0: Design Lock

Before coding:

1. confirm request payload with backoffice
2. confirm callback payload with backoffice
3. confirm idempotency rule using `withdraw_id`
4. choose storage approach for withdraw requests

### Decision note

This project currently stores settings in JSON. That is not a good fit for withdraw jobs.
Recommend introducing a real database table or a dedicated local SQLite store for withdraw requests.

## Phase 1: Persistence Layer

### New file recommendation

Create a dedicated module, for example:

- `withdraw_store.py`

Responsibilities:

- initialize withdraw table
- insert new withdraw request
- fetch by `withdraw_id`
- claim pending work
- update final result
- update callback delivery status

### Tasks

1. choose SQLite for v1 local persistence unless there is already a database standard
2. create `withdraw_requests` schema
3. enforce `UNIQUE(withdraw_id)`
4. add helper methods for:
   - `create_withdraw(...)`
   - `get_withdraw(withdraw_id)`
   - `claim_next_pending(device_serial=None)`
   - `mark_processing(withdraw_id, started_at)`
   - `mark_success(withdraw_id, bank_ref, finished_at)`
   - `mark_fail(withdraw_id, failure_reason, finished_at)`
   - `mark_callback_sent(withdraw_id)`
   - `mark_callback_fail(withdraw_id, reason)`

## Phase 2: API Layer

Target file: [server.py](/Users/csl-dev/Desktop/csl/device-manager/server.py)

### Add endpoints

1. `POST /api/withdraws`
2. `GET /api/withdraws/<withdraw_id>`
3. optional `POST /api/withdraws/<withdraw_id>/retry`

### Tasks in `server.py`

1. add request parsing and validation for withdraw creation
2. delegate all business logic to `eng`
3. return JSON responses for:
   - new accepted request
   - duplicate existing request
   - invalid payload
4. add read endpoint to inspect one withdraw by id

### Important rule

`server.py` should stay thin.
Do not put queueing, idempotency, callback, or automation logic directly in Flask route handlers.

## Phase 3: Engine Service Layer

Target file: [engine.py](/Users/csl-dev/Desktop/csl/device-manager/engine.py)

### Add new responsibilities

1. receive create-withdraw commands from `server.py`
2. enforce idempotent business behavior
3. coordinate withdraw worker lifecycle
4. publish progress events to the SSE stream if useful
5. isolate withdraw flow from monitor flow

### Suggested new methods in `Engine`

- `create_withdraw(data)`
- `get_withdraw(withdraw_id)`
- `retry_withdraw(withdraw_id)` if retry is added
- `start_withdraw_worker()` or internal worker bootstrap
- `_withdraw_loop()`
- `_process_withdraw(job)`
- `_send_withdraw_callback(job, result)`

### Concrete tasks

1. initialize withdraw persistence/store during engine startup
2. add lock protection for withdraw state transitions if needed
3. add method to create a withdraw request safely
4. handle duplicate requests by returning current state
5. start a background worker thread for pending withdraw jobs
6. ensure worker picks only one active job per device at a time
7. update job state transitions:
   - `pending -> processing`
   - `processing -> success|fail`
8. after final state, trigger callback send
9. record callback delivery state separately
10. optionally emit stream events so UI can show progress

### Important separation rule

Do not reuse:

- incoming watermark logic
- `_sent_refs`
- `_send(serial, new)` transaction send path
- monitor thread state

Withdraw is a separate domain and should stay separate in code.

## Phase 4: Mobile Automation Layer

Target file: [bcel.py](/Users/csl-dev/Desktop/csl/device-manager/bcel.py)

### New function recommendation

Add a new dedicated function such as:

- `execute_withdraw(serial, password, username, withdraw_data, log=print)`

### Tasks

1. define the mobile withdraw steps clearly
2. encapsulate the flow in one top-level function
3. return structured result data
4. avoid touching `poll_messages` logic
5. keep selectors and navigation isolated from incoming monitor path

### Return shape recommendation

```python
{
    "ok": True,
    "bank_ref": "ABC12345",
    "message": "withdraw completed"
}
```

or

```python
{
    "ok": False,
    "bank_ref": "",
    "message": "insufficient balance"
}
```

### Safety note

Before implementing real taps that move money, create a dry-run/log-only mode if possible.

## Phase 5: Callback HTTP Client

Preferred location:

- either a new helper module such as `withdraw_client.py`
- or a small extension in [csl_client.py](/Users/csl-dev/Desktop/csl/device-manager/csl_client.py) if you want to keep outbound HTTP helpers together

### Recommendation

Use a separate helper for withdraw callbacks if the callback auth/signature rules differ from the payment gateway rules.

### Tasks

1. implement callback sender
2. send success/fail payload to `callback_url`
3. return delivery result for engine to persist
4. distinguish:
   - final withdraw result
   - callback delivery result

## Phase 6: UI Visibility

Primary UI file: [electron/renderer/app.js](/Users/csl-dev/Desktop/csl/device-manager/electron/renderer/app.js)

Optional supporting UI files:

- [electron/renderer/index.html](/Users/csl-dev/Desktop/csl/device-manager/electron/renderer/index.html)
- [electron/renderer/style.css](/Users/csl-dev/Desktop/csl/device-manager/electron/renderer/style.css)

### Suggested UI tasks

1. add a Withdraws view
2. load withdraw list/status from backend
3. show:
   - withdraw id
   - device
   - amount
   - status
   - callback status
   - failure reason
4. add refresh support
5. optionally add retry button for failed rows

### Suggested backend support if UI is added

Additional endpoint:

- `GET /api/withdraws`

This is not strictly required for v1 API integration, but helpful for operations.

## Phase 7: App Startup and Recovery

Primary file: [engine.py](/Users/csl-dev/Desktop/csl/device-manager/engine.py)

### Tasks

1. decide what happens to stale `processing` jobs on startup
2. initialize withdraw worker when engine starts
3. ensure worker shutdown does not corrupt state

### Recommended first-pass behavior

If app restarts and finds stale `processing` jobs:

- mark them `fail` with reason like `worker_restart`

This is safer than silently retrying a money-moving job.

## Phase 8: Testing

### Unit/logic tests to add

Recommended new test areas:

1. idempotent create with same `withdraw_id`
2. duplicate request returns existing state
3. worker claim logic only picks one row
4. device-level serialization
5. success path updates final state
6. fail path updates final state
7. callback success/fail persistence
8. restart recovery for stale `processing`

### Manual test stages

1. create withdraw request only, no automation
2. simulate worker success
3. simulate worker fail
4. simulate duplicate requests
5. simulate callback failure
6. test single device with one real controlled flow

## Phase 9: Recommended Delivery Sequence

### Milestone 1

- add persistence layer
- add `POST /api/withdraws`
- add `GET /api/withdraws/<withdraw_id>`
- add idempotency enforcement
- no automation yet

### Milestone 2

- add worker loop
- add state transitions
- add callback sender
- still run with mocked or dry-run automation

### Milestone 3

- implement real mobile withdraw flow in `bcel.py`
- test with one device only

### Milestone 4

- add UI visibility
- add retry support
- harden callback retry behavior

## Suggested File-Level Checklist

### [server.py](/Users/csl-dev/Desktop/csl/device-manager/server.py)

- add withdraw create route
- add withdraw get route
- optional retry route

### [engine.py](/Users/csl-dev/Desktop/csl/device-manager/engine.py)

- add withdraw service methods
- add worker thread
- add callback handling
- add restart recovery logic

### [bcel.py](/Users/csl-dev/Desktop/csl/device-manager/bcel.py)

- add withdraw automation entrypoint
- keep separate from `poll_messages`

### [csl_client.py](/Users/csl-dev/Desktop/csl/device-manager/csl_client.py)

- extend only if callback helper belongs here
- otherwise keep payment-gateway logic isolated and create a new helper module

### New file: `withdraw_store.py`

- persistence and idempotency-safe DB access

### Optional new file: `withdraw_client.py`

- callback sender helper

### [electron/renderer/app.js](/Users/csl-dev/Desktop/csl/device-manager/electron/renderer/app.js)

- optional withdraw UI integration

## Recommended Next Step

Start implementation with:

1. `withdraw_store.py`
2. `server.py` create/get endpoints
3. `engine.py` create/get service methods

That gives you a safe integration surface for backoffice before touching real mobile withdraw automation.
