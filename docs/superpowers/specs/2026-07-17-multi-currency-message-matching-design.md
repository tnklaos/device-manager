# Multi-Currency Message Matching Design

## Problem

The active BCEL message poller recognizes amounts only when their currency is
`LAK` or `USD`. Incoming THB and other foreign-currency rows can therefore be
skipped during list classification or rejected during detail verification before
they reach either delivery client.

## Approved Approach

Recognize an amount followed by a three-letter uppercase currency code. This
covers standard displayed currencies such as LAK, USD, THB, CNY, EUR, and VND
without maintaining a fragile allowlist. An explicit allowlist would require a
release for every newly observed currency; accepting an arbitrary word would be
too permissive for money-critical matching.

Use one shared amount pattern throughout the active row parser, source parser,
and detail verifier. Normalize the numeric portion exactly and retain the
currency code. Detail verification must compare both pieces, so an equal numeric
value in a stale detail with a different currency is rejected.

## Preserved Behavior

- SAL rows and rows containing `Mastercard` remain intentionally excluded.
- The detail timestamp and sender/account consistency checks remain mandatory.
- The forwarding clients remain unchanged and receive BCEL's original amount
  string, including its currency.
- Watermarks still advance only according to the existing verified-send rules.

## Tests

- A regular positive THB row is classified as incoming and has a stable key that
  includes its amount and currency.
- A THB list row matches a THB detail with the same value and time.
- Equal numeric values with different currencies do not match.
- Existing LAK/USD behavior and Mastercard/SAL exclusion continue to pass.
