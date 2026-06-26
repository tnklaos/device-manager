# Withdraw Implementation Plan

Temporary planning document for adding an async withdraw flow to Device Manager.
This file is intended as a working plan and can be removed later after implementation.

## Current Agreed Model

This section overrides older assumptions below when they conflict.

1. One device has exactly one role: `deposit` or `withdraw`.
2. `both` is not allowed.
3. Deposit devices continue using the current monitor flow.
4. Withdraw devices are reserved for the new withdraw flow only.
5. Backoffice does not send `device_serial` for withdraw requests.
6. The app routes pending withdraw jobs to the currently active withdraw device.
7. The withdraw request business key is `withdrawalId`.
8. Starting a withdraw device should eventually bring up the withdraw runner and ngrok for inbound requests.

## Development Status (2026-06-25)

Implemented today:

1. `POST /api/withdraws` exists in the backend.
2. Withdraw request logs now include request summary, payload debug object, and device activity snapshot.
3. Device credentials modal now stores a per-device role: `deposit` or `withdraw`.
4. Withdraw devices can be started from the UI and become the active withdraw device placeholder.
5. When a withdraw request arrives and a withdraw device is active, the app currently only opens BCEL One and performs login if needed.
6. If no withdraw device is active, the request is rejected.
7. If the withdraw device is already busy opening the app/login flow, the request is rejected.

Not implemented yet:

1. ngrok auto-start when withdraw device starts
2. queue/database persistence for withdraw requests
3. duplicate protection by `withdrawalId`
4. real withdraw UI automation after app/login
5. callback back to backoffice
6. withdraw status page in the UI

## Goal

Add a withdraw workflow with this behavior:

1. Backoffice confirms an auto-withdraw.
2. Backoffice sends a request to this app's Python API.
3. The API validates and stores the request in the database with status `pending`.
4. The API responds immediately with an accepted/ok response.
5. A background worker processes the withdraw on the mobile UI.
6. When done, the app updates the withdraw status to `success` or `fail`.
7. The app sends a callback to backoffice with the final result.
8. Duplicate requests for the same withdraw must not create duplicate jobs.

## Core Principles

1. Keep withdraw flow separate from the existing incoming transaction monitor flow.
2. Treat withdraw as an async job, not a synchronous API request.
3. Use idempotency at the database level.
4. Process one active withdraw at a time per device.
5. Always keep a clear audit trail for request, processing, result, and callback.

## High-Level Flow

```text
Backoffice admin confirms withdraw
  -> POST /api/withdraws
  -> app validates request
  -> app inserts withdraw row with status=pending
  -> app returns accepted immediately
  -> worker picks pending job
  -> worker updates status=processing
  -> app automates withdraw on mobile
  -> app updates status=success or fail
  -> app POSTs callback to backoffice
```

## Recommended Status Model

Primary job status:

- `pending`
- `processing`
- `success`
- `fail`

Separate callback status:

- `pending`
- `sent`
- `fail`

This keeps job execution status separate from callback delivery status.

## Duplicate Prevention

Duplicate handling is the most important requirement.

### Recommended rule

Use `withdraw_id` from backoffice as the idempotency key.

Examples:

- `withdraw_id`
- `merchant_withdraw_id`
- `request_id`

Best option: require backoffice to always send one unique `withdraw_id`.

### Required protections

1. Database unique constraint on `withdraw_id`
2. API logic that returns the existing row if the same `withdraw_id` is received again
3. Do not create a new job when the existing job is `pending` or `processing`

### Expected duplicate behavior

If the same `withdraw_id` is sent again:

- If current status is `pending`, return that it already exists
- If current status is `processing`, return that it is already in progress
- If current status is `success`, return the completed status
- If current status is `fail`, return the failed status and optionally allow retry via a separate API

## Phase 1: Finalize Business Contract

Before coding, confirm:

1. What fields backoffice will send
2. Whether device selection is provided by backoffice or assigned internally
3. What result fields must be returned in the callback
4. Whether retry is allowed on failed withdraws
5. Whether callback URL is fixed globally or provided per request

### Draft request payload

```json
{
  "withdraw_id": "wd_20260625_000123",
  "device_serial": "emulator-5554",
  "amount": 50000,
  "account_no": "02012345678",
  "account_name": "Example User",
  "bank_name": "BCEL",
  "callback_url": "https://backoffice.example.com/api/withdraw/callback"
}
```

### Draft immediate response

```json
{
  "ok": true,
  "accepted": true,
  "withdraw_id": "wd_20260625_000123",
  "status": "pending"
}
```

### Draft duplicate response

```json
{
  "ok": true,
  "accepted": true,
  "duplicate": true,
  "withdraw_id": "wd_20260625_000123",
  "status": "processing"
}
```

### Draft callback payload

Success:

```json
{
  "withdraw_id": "wd_20260625_000123",
  "status": "success",
  "bank_ref": "ABC12345",
  "failure_reason": "",
  "finished_at": "2026-06-25T10:00:00Z"
}
```

Fail:

```json
{
  "withdraw_id": "wd_20260625_000123",
  "status": "fail",
  "bank_ref": "",
  "failure_reason": "insufficient balance",
  "finished_at": "2026-06-25T10:00:00Z"
}
```

## Phase 2: Database Design

Introduce a dedicated `withdraw_requests` table.

Suggested columns:

- `id`
- `withdraw_id`
- `device_serial`
- `amount`
- `account_no`
- `account_name`
- `bank_name`
- `callback_url`
- `status`
- `callback_status`
- `failure_reason`
- `bank_ref`
- `requested_at`
- `started_at`
- `finished_at`
- `created_at`
- `updated_at`

### Required indexes and constraints

- `UNIQUE(withdraw_id)`
- index on `status`
- index on `(device_serial, status)`

### Important implementation note

Do not rely on:

1. query first
2. insert second

as the only duplicate protection.

That can still race under concurrent requests.

Instead:

1. enforce `UNIQUE(withdraw_id)` at the DB level
2. handle duplicate insert safely in API code

## Phase 3: API Design

Recommended endpoints:

1. `POST /api/withdraws`
2. `GET /api/withdraws/<withdraw_id>`
3. `POST /api/withdraws/<withdraw_id>/retry` (optional)

### `POST /api/withdraws`

Responsibilities:

1. Validate request payload
2. Check idempotency using `withdraw_id`
3. Insert new row with `pending` if it does not exist
4. Return existing status if it already exists
5. Never block waiting for mobile automation result

### `GET /api/withdraws/<withdraw_id>`

Use for:

- backoffice polling
- internal support/debugging
- UI inspection

### `POST /api/withdraws/<withdraw_id>/retry`

Optional rule:

- allow retry only when current status is `fail`

## Phase 4: Engine / Worker Design

Add a withdraw worker path in [engine.py](/Users/csl-dev/Desktop/csl/device-manager/engine.py).

Responsibilities:

1. Poll database for `pending` withdraw jobs
2. Lock one job for processing
3. Update status from `pending` to `processing`
4. Run mobile automation
5. Update final status
6. Trigger callback

### Concurrency rules

1. One active withdraw at a time per device
2. A job must be atomically claimed before processing
3. A `processing` job must not be picked again by another worker

### Recovery rule

Need a decision for app restart behavior:

- On restart, what should happen to jobs left in `processing`?

Recommended initial rule:

- mark stale `processing` jobs back to `pending` on startup only if clearly safe
- or mark them `fail` with reason `worker_restart`

This should be decided before implementation.

## Phase 5: Mobile Automation Design

Add a dedicated withdraw automation function in [bcel.py](/Users/csl-dev/Desktop/csl/device-manager/bcel.py).

Suggested shape:

```python
def execute_withdraw(...):
    return {
        "ok": True,
        "bank_ref": "ABC12345",
        "message": "withdraw completed"
    }
```

Failure example:

```python
def execute_withdraw(...):
    return {
        "ok": False,
        "bank_ref": "",
        "message": "insufficient balance"
    }
```

### Important note

Do not mix withdraw logic into `poll_messages`.
Withdraw should be a separate workflow and code path.

## Phase 6: Callback Design

After job completion:

1. Send callback to backoffice
2. Record callback result in the database
3. Retry callback later if delivery fails

Suggested callback data:

- `withdraw_id`
- `status`
- `bank_ref`
- `failure_reason`
- `finished_at`

### Callback retry

Decide:

1. max retry count
2. retry interval
3. what to do when callback permanently fails

Recommended starting point:

- keep the job result final
- mark `callback_status=fail`
- allow a retry loop or manual resend later

## Phase 7: UI Plan

Optional at first, but recommended later.

Suggested UI features:

1. Withdraw list page
2. Filter by status
3. Inspect failure reason
4. Retry failed jobs
5. Show current device processing state

UI should come after API and worker fundamentals are stable.

## Phase 8: Test Plan

Minimum tests recommended:

1. Create new withdraw request -> row created with `pending`
2. Send same `withdraw_id` again -> no duplicate row
3. Pending job claimed by worker -> status becomes `processing`
4. Success path -> status becomes `success` and callback is sent
5. Fail path -> status becomes `fail` and callback is sent
6. Callback delivery fails -> callback status becomes `fail`
7. Two simultaneous requests for the same `withdraw_id` -> only one row exists
8. Multiple pending jobs for one device -> processed one at a time

## Phase 9: Rollout Plan

Recommended rollout:

1. Log-only mode
   - accept requests
   - save to DB
   - do not execute withdraw yet
2. Test device mode
3. Limited production rollout
4. Full production rollout

This reduces risk before real money movement.

## Suggested Implementation Order

1. Finalize request/response/callback contract
2. Create database schema
3. Add API endpoints in [server.py](/Users/csl-dev/Desktop/csl/device-manager/server.py)
4. Add withdraw service/worker logic in [engine.py](/Users/csl-dev/Desktop/csl/device-manager/engine.py)
5. Add withdraw automation in [bcel.py](/Users/csl-dev/Desktop/csl/device-manager/bcel.py)
6. Add callback sender logic
7. Add basic UI visibility
8. Add tests
9. Roll out in stages

## Open Questions

These should be answered before implementation:

1. Is `withdraw_id` guaranteed unique from backoffice?
2. Which database should be used first: SQLite, MySQL, or PostgreSQL?
3. Does each request already know which device should execute it?
4. Should failed callbacks retry automatically?
5. What is the correct recovery rule for jobs left in `processing` after restart?
6. What exact fields are required for the mobile withdraw flow?
7. Is there any approval or guard step required before the mobile UI confirms the withdraw?

## Recommended First Milestone

Implement only these pieces first:

1. database table
2. `POST /api/withdraws`
3. idempotency by `withdraw_id`
4. `GET /api/withdraws/<withdraw_id>`
5. no automation yet

This creates a safe foundation before touching the money-moving mobile automation path.
