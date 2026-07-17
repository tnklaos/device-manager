# Hide Custom Set Creation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the custom-set creation entry point while preserving existing custom profiles and delivery behavior.

**Architecture:** Remove only the renderer's `new-custom` route and tab. Keep type-aware rendering and saving for custom profiles returned by `/api/sets`.

**Tech Stack:** Electron renderer JavaScript, Python `unittest` contract test.

## Global Constraints

- Existing custom sets remain visible, editable, and assignable.
- Gateway creation remains available through `＋ Add set`.
- Backend APIs and custom forwarding remain unchanged.

---

### Task 1: Remove the Custom Creation Entry Point

**Files:**
- Modify: `tests/test_custom_set_ui.py`
- Modify: `electron/renderer/app.js:174-280`

**Interfaces:**
- Consumes: existing type-aware set objects returned by `/api/sets`.
- Produces: settings tabs without a `new-custom` creation route.

- [x] **Step 1: Write the failing renderer contract test**

Replace the creation-button assertions with assertions that `new-custom`,
`data-set="new-custom"`, and `＋ Custom Set` are absent, while `set-header`,
`set-api-key`, `set-callback`, and the `setType === "custom"` save branch remain.

- [x] **Step 2: Verify RED**

Run: `./venv/bin/python -m unittest tests.test_custom_set_ui -v`

Expected: FAIL because `app.js` still contains the custom creation route/button.

- [x] **Step 3: Remove the renderer route**

Remove `new-custom` from valid tab state, delete the `＋ Custom Set` tab HTML,
and make all new profiles default to `gateway`. Retain existing-profile type
handling so saved custom profiles still render and save correctly.

- [x] **Step 4: Verify GREEN and regressions**

Run: `./venv/bin/python -m unittest tests.test_custom_set_ui -v`

Run: `./venv/bin/python -m unittest discover -s tests -v`

Expected: all tests pass.

- [x] **Step 5: Verify runtime**

Run: `node --check electron/renderer/app.js && git diff --check`

Restart dev and confirm `GET /api/health` returns HTTP 200.
