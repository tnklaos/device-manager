# Withdraw Spec v1

Temporary functional and API specification for async withdraw support in Device Manager.
This document is intended for alignment between backoffice, backend, and app implementation teams.

## Current Agreed Model

This section takes priority over older examples below when they differ.

1. Device role is exclusive: `deposit` or `withdraw`.
2. Backoffice payload uses `withdrawalId`, not `withdraw_id`.
3. Backoffice does not send `device_serial`.
4. The app chooses the currently active withdraw device at processing time.
5. There can be only one active withdraw device at a time.
6. Deposit flow remains the existing monitor flow.

## Development Status (2026-06-25)

Current backend behavior:

1. `POST /api/withdraws` accepts a request body and returns an immediate JSON response.
2. If there is no active withdraw device, the backend returns `409`.
3. If a withdraw device is already busy in the `open app/login` step, the backend returns `409`.
4. If a withdraw device is active and idle, the backend starts a background prepare step that only opens BCEL One and logs in if required.

Current UI/device behavior:

1. Device credentials now support a saved role of `deposit` or `withdraw`.
2. Starting a `deposit` device uses the old monitor flow.
3. Starting a `withdraw` device marks it active, but does not yet perform withdraw business logic.

Current logging behavior:

1. Withdraw requests log a text summary to the backend/activity log.
2. Withdraw requests also emit a structured debug object that includes:
   - request payload
   - device activity snapshot
   - reject message when applicable

## 1. Objective

Add an asynchronous withdraw workflow to Device Manager so that:

1. Backoffice confirms an auto-withdraw request.
2. Backoffice sends a request to this app's Python API.
3. The app accepts the request immediately and stores it with status `pending`.
4. The app later performs the withdraw automation on the assigned mobile device.
5. The app updates the withdraw result to `success` or `fail`.
6. The app sends a callback to backoffice with the final result.

This workflow must be idempotent and must not create duplicate withdraw jobs when the same request is resent.

## 2. Scope

Included in v1:

- receive withdraw request from backoffice
- persist withdraw request
- prevent duplicates using a business id
- process withdraw asynchronously
- send result callback to backoffice
- expose current withdraw status for support/debugging

Not included in v1:

- batch withdraw submission
- partial success flows
- multi-step approval inside the app
- reconciliation with bank statement
- retrying failed mobile steps without creating a new business event

## 3. Architecture Summary

The existing app already has:

- Electron renderer UI
- Flask API in [server.py](/Users/csl-dev/Desktop/csl/device-manager/server.py)
- business logic in [engine.py](/Users/csl-dev/Desktop/csl/device-manager/engine.py)
- mobile automation logic in [bcel.py](/Users/csl-dev/Desktop/csl/device-manager/bcel.py)

Withdraw v1 should be implemented as a separate async job path and must not be mixed into the current incoming transaction monitor pipeline.

## 4. Business Definitions

### 4.1 Withdraw Request

A withdraw request is a command from backoffice instructing the app to execute one withdraw on one mobile device.

### 4.2 Idempotency Key

`withdraw_id` is the unique business identifier for one withdraw request.

Rules:

- backoffice must generate `withdraw_id`
- the app must treat `withdraw_id` as globally unique
- the app must not create more than one withdraw job for the same `withdraw_id`

### 4.3 Async Acceptance

The create-withdraw API must return immediately after validation and persistence.

Important:

- `accepted` does not mean the withdraw succeeded
- `accepted` only means the app has stored the request for later processing

## 5. Status Model

### 5.1 Job Status

- `pending`
  - request accepted and stored
  - waiting for worker execution
- `processing`
  - worker claimed the job
  - mobile automation is in progress
- `success`
  - mobile automation finished successfully
- `fail`
  - mobile automation finished unsuccessfully

### 5.2 Callback Status

- `pending`
  - callback not attempted yet
- `sent`
  - callback delivered successfully
- `fail`
  - callback delivery failed

Callback status is separate from job status.

## 6. API Contract

## 6.1 Create Withdraw

Endpoint:

```text
POST /api/withdraws
```

Purpose:

- receive a new withdraw request from backoffice
- guarantee idempotent create behavior

### Request body

```json
{
  "withdraw_id": "wd_20260625_000123",
  "device_serial": "R58N123456A",
  "amount": 50000,
  "account_no": "02012345678",
  "account_name": "Example User",
  "bank_name": "BCEL",
  "callback_url": "https://backoffice.example.com/api/withdraw/callback",
  "note": "optional reference from backoffice"
}
```

### Required fields

- `withdraw_id`
- `device_serial`
- `amount`
- `account_no`
- `account_name`
- `callback_url`

### Validation rules

- `withdraw_id` must be non-empty
- `device_serial` must be non-empty
- `amount` must be positive
- `account_no` must be non-empty
- `account_name` must be non-empty
- `callback_url` must be a valid HTTPS URL unless internal non-HTTPS is explicitly allowed

### Success response for new request

```json
{
  "ok": true,
  "accepted": true,
  "duplicate": false,
  "withdraw_id": "wd_20260625_000123",
  "status": "pending"
}
```

### Success response for duplicate request

```json
{
  "ok": true,
  "accepted": true,
  "duplicate": true,
  "withdraw_id": "wd_20260625_000123",
  "status": "processing"
}
```

### Behavior rules

If `withdraw_id` does not exist:

- create a new row with status `pending`
- return `accepted=true`

If `withdraw_id` already exists:

- do not create a new row
- do not reset the existing state
- return the existing state

### Error response example

```json
{
  "ok": false,
  "accepted": false,
  "message": "invalid callback_url"
}
```

## 6.2 Get Withdraw Status

Endpoint:

```text
GET /api/withdraws/<withdraw_id>
```

Purpose:

- allow backoffice or support tools to inspect a withdraw state

### Response example

```json
{
  "ok": true,
  "withdraw": {
    "withdraw_id": "wd_20260625_000123",
    "device_serial": "R58N123456A",
    "amount": 50000,
    "account_no": "02012345678",
    "account_name": "Example User",
    "status": "processing",
    "callback_status": "pending",
    "bank_ref": "",
    "failure_reason": "",
    "requested_at": "2026-06-25T10:00:00Z",
    "started_at": "2026-06-25T10:01:00Z",
    "finished_at": null
  }
}
```

## 6.3 Retry Failed Withdraw

Optional for v1:

```text
POST /api/withdraws/<withdraw_id>/retry
```

Rule:

- only allowed when current status is `fail`
- must not create a second business record
- should either reset the same row back to `pending` or create an internal retry counter

For the first implementation, this endpoint may be deferred.

## 7. Callback Contract

After the worker finishes, the app must callback to the request's `callback_url`.

## 7.1 Callback on success

```json
{
  "withdraw_id": "wd_20260625_000123",
  "status": "success",
  "bank_ref": "ABC12345",
  "failure_reason": "",
  "finished_at": "2026-06-25T10:05:10Z"
}
```

## 7.2 Callback on failure

```json
{
  "withdraw_id": "wd_20260625_000123",
  "status": "fail",
  "bank_ref": "",
  "failure_reason": "insufficient balance",
  "finished_at": "2026-06-25T10:05:10Z"
}
```

## 7.3 Callback rules

- callback is attempted after final job status is written
- callback failure must not revert the final withdraw result
- callback delivery result must be tracked separately in `callback_status`

## 8. Database Contract

Recommended table: `withdraw_requests`

### Suggested columns

- `id`
- `withdraw_id`
- `device_serial`
- `amount`
- `account_no`
- `account_name`
- `bank_name`
- `callback_url`
- `note`
- `status`
- `callback_status`
- `failure_reason`
- `bank_ref`
- `requested_at`
- `started_at`
- `finished_at`
- `created_at`
- `updated_at`

### Constraints

- `withdraw_id` must be unique

### Indexes

- index on `status`
- index on `device_serial, status`

## 9. Duplicate and Idempotency Rules

This is the most important rule set in withdraw v1.

### 9.1 Create duplicate protection

When the app receives a second `POST /api/withdraws` with the same `withdraw_id`:

- it must not create another job
- it must not trigger another automation job
- it must return the current status of the existing request

### 9.2 Do not dedupe by amount/account

The app must not dedupe using:

- amount
- account number
- account name
- callback URL

Those fields may legitimately repeat across multiple withdraws.

Only `withdraw_id` is the idempotency key.

### 9.3 Race condition protection

Checking for duplicates only in memory is not enough.

Required:

- database unique constraint
- duplicate-safe insert logic

## 10. Worker Behavior

The withdraw worker should run inside the Python app, most likely in [engine.py](/Users/csl-dev/Desktop/csl/device-manager/engine.py).

### Rules

- one device can process only one withdraw at a time
- worker must atomically claim a `pending` job
- claimed jobs become `processing`
- worker runs mobile automation
- worker writes final status
- worker triggers callback

## 11. Mobile Automation Contract

The mobile automation logic should live in [bcel.py](/Users/csl-dev/Desktop/csl/device-manager/bcel.py) as a new dedicated flow.

Suggested return shape:

```python
{
    "ok": True,
    "bank_ref": "ABC12345",
    "message": "withdraw completed"
}
```

Failure example:

```python
{
    "ok": False,
    "bank_ref": "",
    "message": "insufficient balance"
}
```

Withdraw automation must stay separate from `poll_messages`.

## 12. Recovery Rules

Open design choice for v1:

What should happen to rows stuck in `processing` if the app crashes or restarts?

Recommended conservative choices:

Option A:

- mark stale `processing` rows as `fail` with reason `worker_restart`

Option B:

- move stale `processing` rows back to `pending` only if the business accepts possible re-run risk

My recommendation for v1:

- prefer `fail` over automatic re-run unless there is a strong reason to retry automatically

## 13. Security and Audit

Every withdraw should be traceable.

Recommended audit fields:

- who requested it, if available
- when it was received
- which device executed it
- when execution started
- when execution finished
- final status
- callback result

## 14. Non-Goals and Safety Notes

Withdraw flow must not:

- reuse the incoming transaction watermark logic
- reuse `sent_refs` dedup logic from the incoming monitor
- block the create API until mobile execution finishes

Withdraw flow should be implemented as a separate domain inside the existing app.

## 15. Recommended First Implementation Milestone

Build these pieces first:

1. database table
2. `POST /api/withdraws`
3. duplicate prevention by `withdraw_id`
4. `GET /api/withdraws/<withdraw_id>`
5. no mobile automation yet

This provides a safe contract for backoffice integration before touching the money-moving device path.
