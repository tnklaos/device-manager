# Development Status

Last updated: 2026-06-25

This file tracks the current development status of the withdraw workstream and related UI/backend changes.

## Current Scope

Current focus:

1. Add withdraw device role support
2. Receive withdraw requests from backoffice
3. Prepare the active withdraw device by opening BCEL One and logging in
4. Reject requests safely when the withdraw device is not ready

## Done Today

### Device role and settings

1. Added per-device role support: `deposit` or `withdraw`
2. Updated the Device Credentials modal to include a role selector
3. Saved device role through the backend device credentials API
4. Displayed the current role on each device card

### Withdraw request endpoint

1. Added `POST /api/withdraws`
2. Logged withdraw request summary
3. Logged request payload in debug output
4. Logged device activity snapshot when a withdraw request arrives
5. Logged each device role and whether it is started or idle

### Withdraw device start behavior

1. Allowed a `withdraw` device to be started from the UI
2. Marked a started withdraw device as active in app state
3. Kept deposit devices on the existing monitor flow

### Withdraw request handling

1. When a request arrives and a withdraw device is active, the app now opens BCEL One
2. The app uses the saved device credentials and logs in if the login screen appears
3. This step runs in the background and does not yet perform real withdraw actions

### Safe rejection behavior

1. Reject request if no active withdraw device is started
2. Reject request if a withdraw device is already busy opening the app/login flow
3. Return a clear API message for these rejected cases

## Not Done Yet

### Withdraw core flow

1. Real withdraw navigation after app open/login
2. Fill withdraw form fields
3. Submit withdraw action
4. Confirm success/fail result from the mobile UI

### Request management

1. Save withdraw requests in SQLite or another local database
2. Prevent duplicate requests using `withdrawalId`
3. Add pending/processing/success/fail state management
4. Add retry behavior

### Connectivity and callback

1. Auto-start ngrok when a withdraw device starts
2. Register or manage a withdraw callback URL
3. Send callback to backoffice after success/fail

### UI and operations

1. Add a withdraw status page
2. Show queued or rejected withdraw requests in the UI
3. Add better debug console organization if needed

## Current Behavior Summary

As of today:

1. A withdraw request can be received by the backend
2. The backend can inspect current device activity
3. If a withdraw device is active and not busy, the app opens BCEL One and logs in
4. If no withdraw device is active, or if the device is already preparing, the request is rejected
5. No real withdraw business action happens yet

## Recommended Next Step

Best next implementation step:

1. Add local persistence for withdraw requests
2. Enforce duplicate protection with `withdrawalId`
3. Then continue to the real withdraw UI automation flow
