# Hide Custom Set Creation Design

## Decision

Remove the `＋ Custom Set` entry point and its `new-custom` renderer route. This
is preferred over deleting the feature or adding a new configuration flag: it
satisfies the requested UI restriction without changing stored profiles or the
delivery pipeline.

## Preserved Behavior

- Existing custom-set tabs remain visible and editable.
- Existing custom sets remain assignable from every device card.
- Custom API-key redaction and unsigned callback delivery remain unchanged.
- Gateway-set creation through `＋ Add set` remains available.
- The backend continues to accept custom-set records for compatibility.

## Verification

The renderer contract test must prove that no `new-custom` creation route or
`＋ Custom Set` button is rendered, while custom editor fields and custom save
handling remain present for existing profiles.
