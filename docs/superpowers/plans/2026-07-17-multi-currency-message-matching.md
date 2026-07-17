# Multi-Currency Message Matching Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Forward verified incoming BCEL transfers displayed in any standard three-letter currency while preserving currency-safe detail matching.

**Architecture:** Define one shared active-path amount token in `bcel.py` and use it for list rows, source boundaries, and detail identity. Detail verification compares an exact normalized decimal value together with its currency code; delivery code remains unchanged.

**Tech Stack:** Python 3, `decimal.Decimal`, `re`, `unittest`.

## Global Constraints

- SAL and Mastercard rows remain excluded.
- Timestamp and sender/account detail verification remain mandatory.
- Original BCEL amount strings continue to the delivery client unchanged.
- No watermark or delivery-client behavior changes.

---

### Task 1: Currency-Aware Active Message Matching

**Files:**
- Modify: `tests/test_bcel_message_matching.py`
- Modify: `bcel.py:390-420,736-789`

**Interfaces:**
- Consumes: BCEL row/detail strings containing `<number> <ISO currency>`.
- Produces: `_amount_identity(text) -> tuple[Decimal, str] | None` and currency-aware row keys/detail matching.

- [x] **Step 1: Write failing regression tests**

Add a THB hierarchy fixture and tests asserting its row is incoming with key
`TRI|10:15:22|1,500.00 THB`. Add detail tests asserting `1,500.00 THB` matches
`1,500 THB`, while `1,500 USD` does not match `1,500 THB`.

- [x] **Step 2: Verify RED**

Run: `./venv/bin/python -m unittest tests.test_bcel_message_matching -v`

Expected: THB row key/classification and THB detail-match tests fail because the active regex accepts only LAK/USD.

- [x] **Step 3: Implement the shared matcher**

Define `_AMOUNT_TOKEN` for an optional ASCII/Unicode minus sign, formatted decimal number, and uppercase three-letter currency. Compile it once for row parsing. Implement `_amount_identity` with `Decimal`, normalizing commas, spaces, and Unicode minus. Use the token in `row_source`, `_list_rows`, and `detail_matches_row`.

- [x] **Step 4: Verify GREEN and regressions**

Run: `./venv/bin/python -m unittest tests.test_bcel_message_matching -v`

Run: `./venv/bin/python -m unittest discover -s tests -v`

Expected: all tests pass, including existing LAK/USD and Mastercard exclusions.

- [x] **Step 5: Verify syntax and runtime**

Run: `./venv/bin/python -m py_compile bcel.py && git diff --check`

Restart the dev app and confirm `GET /api/health` reports version `1.0.6`.
