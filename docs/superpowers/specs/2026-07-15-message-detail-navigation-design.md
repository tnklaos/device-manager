# Message Detail Navigation Design

## Problem

After the monitor opens and reads a BCEL message detail, BCEL v4.31 can remain
on that detail page. This build exposes the detail back control as the unlabeled
resource id `titleprev`; it does not expose a `Close` text element, and Android
Back does not reliably dismiss the WebView detail. When the detail remains open,
the next list-row scan finds no message rows.

## Design

Add a focused `close_message_detail(d)` helper in `bcel.py`. It will:

1. Prefer the version-stable WebView control with resource id `titleprev`.
2. Retain the legacy `Close` text control as a compatibility fallback.
3. Use Android Back only as a final fallback.
4. Wait for `_list_rows(d)` to become non-empty before reporting success.
5. Return a boolean so callers can distinguish a restored list from a failed
   navigation attempt.

`_read_verified_detail` will call this helper whenever a detail was opened,
whether detail verification succeeded or timed out. If the helper cannot restore
the list, the current read attempt will not be accepted; the poll will retain the
existing watermark and retry safely on a later cycle.

`open_messages_tab` will use the same helper when it detects a lingering detail,
so startup and per-row cleanup follow one navigation path.

## Safety and Testing

Unit tests will model the v4.31 page where `Close` is absent and `titleprev`
restores rows. They will also cover failure to restore the list, ensuring an
otherwise verified record is not returned while the device remains on detail.

Live verification on `R8YL10AHMTK` will open an incoming row, read it, invoke the
new close path, and confirm that message rows are visible again. No transaction
will be sent during this navigation-only check.
