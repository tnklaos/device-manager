# Message Detail Navigation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reliably return BCEL v4.31 from a transaction detail to the Messages list after every detail read.

**Architecture:** Add one navigation helper that identifies the real Messages list by both its `titlecontext` refresh control and parsed rows. Route detail cleanup and startup recovery through that helper, and reject a verified record if list restoration fails so the watermark remains safe.

**Tech Stack:** Python 3, uiautomator2 XPath selectors, `unittest`, `unittest.mock`.

## Global Constraints

- Use resource ids and accessibility selectors; do not introduce fixed coordinates.
- Never accept a transaction or advance the watermark while the device remains on a detail page.
- Preserve legacy `Close` and Android Back fallbacks for older BCEL builds.

---

### Task 1: Restore the Messages list after detail reads

**Files:**
- Modify: `bcel.py:559-581,745-798`
- Test: `tests/test_bcel_message_matching.py`

**Interfaces:**
- Consumes: `_list_rows(d) -> list[dict]`, uiautomator2 device selectors.
- Produces: `_message_list_visible(d) -> bool` and `close_message_detail(d, timeout=5, poll=0.2) -> bool`.

- [ ] **Step 1: Write failing navigation tests**

Add tests that simulate `titleprev` restoring `titlecontext` plus message rows, and ensure `_read_verified_detail` returns `None` when cleanup cannot restore the list.

```python
class MessageDetailNavigationTests(unittest.TestCase):
    def test_titleprev_restores_message_list(self):
        device = mock.Mock()
        state = {"list": False}
        titleprev = mock.Mock()
        titleprev.exists = True
        titleprev.click.side_effect = lambda: state.update(list=True)
        titlecontext = mock.Mock()
        type(titlecontext).exists = mock.PropertyMock(
            side_effect=lambda: state["list"]
        )
        device.xpath.side_effect = lambda query: (
            titleprev if "titleprev" in query else titlecontext
        )

        with (mock.patch.object(bcel, "_list_rows", side_effect=lambda _: [{}] if state["list"] else []),
              mock.patch.object(bcel.time, "sleep")):
            self.assertTrue(bcel.close_message_detail(device))

        titleprev.click.assert_called_once_with()

    def test_verified_detail_is_rejected_when_list_cannot_be_restored(self):
        row = {"key": "TRI|10:15:22|50,000 LAK", "center": (360, 500),
               "sig": "TRI\n10:15:22\n50,000 LAK"}
        rec = {"type": "ໄດ້ຮັບເງິນໂອນ", "time": "15/07/2026 10:15:22",
               "amount_in": "50,000.00 LAK", "ref": "125"}
        device = mock.MagicMock()

        with (mock.patch.object(bcel, "_list_rows", return_value=[row]),
              mock.patch.object(bcel, "_extract_message_detail", return_value=rec),
              mock.patch.object(bcel, "detail_matches_row", return_value=True),
              mock.patch.object(bcel, "close_message_detail", return_value=False, create=True),
              mock.patch.object(bcel.time, "time", side_effect=[0, 0, 0, 0]),
              mock.patch.object(bcel.time, "sleep")):
            self.assertIsNone(bcel._read_verified_detail(device, row, log=lambda _: None))
```

- [ ] **Step 2: Run the new tests and verify RED**

Run: `./venv/bin/python -m unittest tests.test_bcel_message_matching.MessageDetailNavigationTests -v`

Expected: failures because `close_message_detail` does not exist and `_read_verified_detail` still accepts a record after the old cleanup path.

- [ ] **Step 3: Implement the minimal navigation helper**

Add `_message_list_visible` and `close_message_detail`. Try `titleprev`, then `Close`, then Android Back, waiting after each action for both `titlecontext` and `_list_rows`.

```python
def _message_list_visible(d):
    return (d.xpath('//*[@resource-id="titlecontext"]').exists
            and bool(_list_rows(d)))


def close_message_detail(d, timeout=5, poll=0.2):
    if _message_list_visible(d):
        return True

    actions = (
        lambda: d.xpath('//*[@resource-id="titleprev"]'),
        lambda: d(text="Close"),
    )
    for get_control in actions:
        try:
            control = get_control()
            if not control.exists:
                continue
            control.click()
        except Exception:
            continue
        deadline = time.time() + timeout
        while time.time() < deadline:
            if _message_list_visible(d):
                return True
            time.sleep(poll)

    try:
        d.press("back")
    except Exception:
        return False
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _message_list_visible(d):
            return True
        time.sleep(poll)
    return _message_list_visible(d)
```

Replace `open_messages_tab`'s inline `Close` handling with `close_message_detail(d)`. Replace `_read_verified_detail`'s inline cleanup with:

```python
if opened and not close_message_detail(d):
    log("⚠ detail closed unsuccessfully — message list was not restored")
    return None
```

- [ ] **Step 4: Run focused and full tests and verify GREEN**

Run: `./venv/bin/python -m unittest tests.test_bcel_message_matching.MessageDetailNavigationTests -v`

Expected: both navigation tests pass.

Run: `./venv/bin/python -m unittest discover -s tests -v`

Expected: all tests pass with zero failures.

- [ ] **Step 5: Verify on the target device**

Stop the monitor to prevent concurrent UI changes, call `_read_verified_detail` on one currently visible incoming row, and confirm the function returns while `_message_list_visible(d)` is true. Do not call the gateway send path.

- [ ] **Step 6: Review the diff**

Run: `git diff --check && git diff -- bcel.py tests/test_bcel_message_matching.py`

Expected: no whitespace errors; only the navigation helper, its two call sites, and focused tests are changed.
