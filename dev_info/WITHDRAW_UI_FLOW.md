# Withdraw UI Flow

This document summarizes the current BCEL One withdrawal flow based on the code currently used in `engine.py` and `bcel.py`.

## 1. Main Flow

The backend performs these steps when preparing a withdrawal device:

1. Check the current BCEL One page and log in only when necessary.
2. Select the UnionPay card.
3. Verify the `Transfer Money` page.
4. Select the source account.
5. Verify the `Receiver Account` page.
6. Enter the destination account.
7. Click the button to continue to the next page.
8. Verify the `Transfer Amount` page.
9. Enter the transfer amount.
10. Click the button to continue to the next page.
11. Handle the `Security Answer` page if it appears.
12. Handle the `Transfer Description` page if it appears.
13. Verify the `Transfer Confirmation` page.
14. Click the `ໂອນເງິນ` / `Transfer` button.
15. Distinguish the `success page` from an `error modal`.
16. On success, click `ສໍາເລັດ` / `Done` and verify that the app returns home.
17. On failure, read the modal message and raise an error.
18. If the flow fails along the way, attempt to recover and return home.

Flow entry points:
- `engine.Engine._prepare_withdraw_device(...)` manages device connection, recovery, and locking.
- `bcel.run_withdraw_flow(...)` runs the withdrawal pages in order.

## 2. Initialization When a Request Is Received

Every withdrawal request must include `clientId`. The engine matches this value
to the existing Gateway Set's Client ID, then finds the started withdraw device
assigned to that same set. Each device has its own in-progress guard, so
separate clients can run at the same time when they use different Android devices.

`connect(...)` no longer restarts the app for every request. It checks the current state first:

1. If `fresh=True`:
   - Restart the app.
2. If the current package is already BCEL:
   - Close any popup blocking the page.
   - Log in if the login screen is displayed.
   - Reuse the current state immediately if the app is already home.
   - Attempt `go_home(...)` if the app is in the middle of another flow or page.
3. If the app is not currently in BCEL:
   - Run `app_start(...)`.

Summary:
- The app does not log in again when it is already on the home page.
- The existing app state is reused while the session remains active.
- Login occurs only when the login screen is actually detected.

## 3. Recognized Pages

### 3.1 Transfer Money

Recognized page titles:
- `ການໂອນເງິນ`
- `Transfer Money`

System actions:
- Verify that the transfer page is displayed.
- Find the source account using the saved account number.
- Click that account's row.

### 3.2 Receiver Account

Recognized page titles:
- `ບັນຊີປາຍທາງ`
- `Receiver Account`
- `Receivers Account`

System actions:
- Verify that the destination account page is displayed.
- Enter `toAccount` from the request.
- Click the button to continue to the next page.

### 3.3 Transfer Amount

Page verification criteria:
- `ຈາກບັນຊີ` / `From Account` section.
- `ຫາບັນຊີ` / `To Account` section.
- The recipient name must match `toName`.

System actions:
- Verify that the source account matches.
- Verify that the destination account matches.
- Verify that the recipient name matches.
- Enter the transfer amount.
- Click the button to continue to the next page.

### 3.4 Security Answer

Page verification criteria:
- The title is `ຢັ້ງຢືນຕົວຕົນເພີ່ມເຕີມ`.
- Or `pageq1/pageq2/pageq3` is present.
- Or `ans1/ans2/ans3` is present.

Question indicators:
- `ຄໍາຖາມທີ 1/2/3`
- `ຄຳຖາມທີ 1/2/3`
- `Question 1/2/3`

Input fields:
- `ans1`
- `ans2`
- `ans3`

System actions:
1. If the security answer page is displayed, determine the current question index.
2. First, try typing the answer at the active cursor.
3. Click the button to continue to the next page.
4. Check whether the page changed.
5. If it did not change, fall back to clicking the actual input (`ans1/ans2/ans3`) and type the answer again.
6. Repeat until all questions are completed.

Supported number of pages:
- 0 pages: skip this step.
- 1 page: enter answer 1 and finish.
- 2 pages: answer 1 -> answer 2.
- 3 pages: answer 1 -> answer 2 -> answer 3.

### 3.5 Transfer Description

Recognized page titles:
- `ຄໍາອະທິບາຍການໂອນ`
- `ຄຳອະທິບາຍການໂອນ`
- `Transfer Description`

Recognized placeholders:
- `ປ້ອນຄໍາອະທິບາຍ`
- `ປ້ອນຄຳອະທິບາຍ`

Expected input matches:
- `rid=desc`
- `class=android.widget.EditText`

System actions:
1. When the description page is detected, first try typing into the active input.
2. If the active-input path does not work, fall back to finding the input below the title or placeholder.
3. Prefer `rid=desc` as the first option.
4. Enter the `remark` value.
5. Click the button to continue to the next page.

### 3.6 Transfer Confirmation

Verification criteria:
- `ຈາກບັນຊີ` / `From Account` section.
- `ຫາບັນຊີ` / `To Account` section.
- The recipient name must match `toName`.

Approach:
- Use the same verification conditions as the `Transfer Amount` page.
- Log `confirm page verified` after successful verification.
- Then click the `ໂອນເງິນ` / `Transfer` button.
- After clicking, first determine whether the result is a `success page` or an `error modal`.

### 3.7 Transfer Success

Example indicators:
- A title such as `ໂອນເງິນສໍາເລັດ`.
- The `ສໍາເລັດ` / `Done` button.
- Receipt information such as `ເລກ Ticket`.

System actions:
1. Log `transfer success` when the success page is detected.
2. Click the `ສໍາເລັດ` / `Done` button.
3. Check whether the app has returned to the home page.
4. Log `current page: home` after the app returns home.

Note:
- The success page is not interpreted as a failure modal.

### 3.8 Transfer Error Modal

Example modal buttons:
- `ຕົກລົງ`
- `OK`
- `Close`
- `ປິດ`

System actions:
1. Read the message in the center of the modal.
2. Log `transfer failed: ...`.
3. Raise the error back to the engine.

Note:
- The `remark` value is read from the following request fields in order:
  - `remark`
  - `note`
  - `description`

## 4. Continue Button

The helper currently recognizes these buttons:
- `ຕໍ່ໄປ`
- `Next`
- `ເພີ່ມບັນຊີ`
- `Add Account`

Reason:
- Some pages use `Next`.
- The final page of some security flows uses `Add Account`.

## 5. Fallback Strategy

Current approach:

1. First, try the `active cursor` on pages where the real UI usually focuses the field automatically.
2. If this path does not change the page, fall back to locating the input in the UI hierarchy.
3. When locating an input in the hierarchy:
   - Prefer `resource-id`.
   - If unavailable, use the label, hint, class, or position below the text.

## 6. Failure Recovery

If the flow fails along the way, the `engine` calls `recover_to_bcel_home(...)`.

Recovery actions:
1. First, attempt to close any popup using:
   - `ຕົກລົງ`
   - `OK`
   - `Close`
   - `ປິດ`
2. If the app is still in BCEL, run `go_home(...)`.
3. Log `back to home` after successfully returning home.

Summary:
- After a failure, the app does not remain stuck in the middle of the flow if it can return home.
- This gives the next request a more predictable starting state.

## 7. Updated Logs

Logs are now shorter to make the flow easier to read, with clearer success and failure messages after a transfer.

Examples:
- `reuse current BCEL home`
- `resume BCEL from .PageActivity`
- `verified transfer money page`
- `verified receiver account page`
- `entered receiver account from request: ...1234`
- `verified transfer amount page: ...`
- `entered transfer amount: 50000`
- `security question 1`
- `answer 1 entered`
- `security answers done`
- `transfer description`
- `description entered`
- `clicking receiver next: ຕໍ່ໄປ`
- `confirm page verified`
- `click transfer: ໂອນເງິນ`
- `transfer success`
- `current page: home`
- `transfer failed: ...`
- `back to home`

## 8. Known Areas to Monitor

1. Some WebView pages have an active cursor but an unclear UI hierarchy.
2. Some inputs are custom controls even though they look like `EditText` fields.
3. ADB keyboard `clear=True` has failed on some pages.
4. Therefore, the current flow prefers key-event clearing with `send_keys(..., clear=False)`.
5. The success page and failure modal must be distinguished using multiple anchors; the presence of a `Close` button alone is not sufficient.

## 9. Related Files

- `engine.py`
  - `Engine._prepare_withdraw_device(...)`
- `bcel.py`
  - `run_withdraw_flow(...)`
  - `open_withdraw_transfer_page(...)`
  - `complete_withdraw_receiver_page(...)`
  - `complete_withdraw_amount_page(...)`
  - `complete_withdraw_security_pages(...)`
  - `complete_withdraw_description_page(...)`
  - `complete_withdraw_confirmation_page(...)`
  - `complete_withdraw_result_page(...)`
  - `connect(...)`
  - `verify_transfer_money_page(...)`
  - `select_source_account_on_transfer_page(...)`
  - `verify_receiver_account_page(...)`
  - `input_receiver_account(...)`
  - `verify_transfer_amount_page(...)`
  - `input_transfer_amount(...)`
  - `input_security_answer(...)`
  - `input_transfer_description(...)`
  - `verify_transfer_confirmation_page(...)`
  - `click_transfer_confirm(...)`
  - `check_transfer_result_modal(...)`
  - `recover_to_bcel_home(...)`
  - `click_receiver_next(...)`
