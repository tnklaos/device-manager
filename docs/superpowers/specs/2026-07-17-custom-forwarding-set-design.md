# Custom Forwarding Set Design

## Goal

Add a second set type that forwards verified incoming BCEL transactions directly
to a client callback without CSL Payment Gateway authentication, MD5 signing, or
remote credential verification during save.

## User Interface

The Settings tab bar keeps `+ Add set` for CSL gateway profiles and adds
`+ Custom Set` beside it. A custom set editor contains:

- Name
- Header
- API-KEY
- Callback URL

Existing custom sets are shown in the same tab bar and device assignment dropdown
as gateway sets. The editor identifies them as custom sets. The API key is a
password field: API responses expose only `has_secret`, and leaving the field
blank while editing preserves the saved value.

Saving a custom set performs local validation only. It does not call the callback,
`/bcel/setup`, or `https://paymentgateway.108pay.co`. Header must be a valid HTTP
header name, Callback URL must use HTTP or HTTPS, and all four fields are required
when creating a custom set. An existing saved API key satisfies the API-KEY
requirement during edits.

## Persistence and API

Each entry in `settings["sets"]` gains `type`, defaulting to `gateway` for legacy
records. Gateway records keep their current fields. Custom records use:

```json
{
  "type": "custom",
  "name": "Client A",
  "header": "x-api-key",
  "api_key": "secret value",
  "callback_url": "https://client.example.com/bcel"
}
```

`GET /api/sets` returns the type, name, header, callback URL, and `has_secret`,
but never the API key. `POST /api/sets` accepts the type-specific fields. Gateway
save and webhook registration behavior remains unchanged. Calling webhook
registration for a custom set returns an explanatory local error and never makes
an outbound request.

## Delivery

Device assignment, transaction verification, global reference deduplication,
watermark behavior, transaction logging, and SSE events remain shared. The send
path dispatches by the assigned set type.

A custom set sends one batch with:

```http
POST <callback_url>
Content-Type: application/json
<header>: <api_key>
```

```json
{
  "transactions": [
    {
      "serial": "R8YL10AHMTK",
      "type": "ໄດ້ຮັບເງິນໂອນ",
      "kind": "TRI",
      "from_account": "02012345678",
      "from_name": "SENDER NAME",
      "to_account": "02212345678",
      "details": "payment note",
      "ref": "202607153926942",
      "amount_in": "50,000.00 LAK",
      "time": "17/07/2026 09:30:00"
    }
  ]
}
```

The payload is normalized and does not include BCEL's legacy `raw` array or CSL
gateway-only field layout. `to_account` is derived from the verified transaction's
receiving account; `amount_in` is populated from the incoming amount, with the
existing generic amount as fallback.

No `client-id`, `hash-signature`, or MD5 logic is used for custom delivery.

## Result Semantics

- Any HTTP 2xx response: success. Mark references sent and allow watermark
  advancement.
- Network error, timeout, or HTTP 5xx: transient failure. Hold the watermark and
  retry next cycle.
- HTTP 4xx: permanent rejection. Log the response and allow watermark advancement
  so one bad record cannot block the device.

Response bodies are optional. Logs include the HTTP status and a bounded response
preview without exposing the configured API key.

## Testing

Tests cover legacy gateway-set migration, secret redaction and blank-on-edit,
custom-save validation without network access, normalized payload and custom
header construction, absence of CSL signature headers, 2xx/4xx/5xx/timeout
semantics, send-path dispatch, and preservation of gateway behavior. Renderer
verification covers both add buttons, type-specific fields, no verification call
after custom save, and custom sets in device assignment.
