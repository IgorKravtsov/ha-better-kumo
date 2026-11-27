# Better Kumo relay API used by this integration

This is the contract between this integration and the Better Kumo relay
([`better-kumo-app`](https://github.com/IgorKravtsov/better-kumo-app), crate `kumo-relay`, HTTP on port 8787).
Parts marked **existing** were in the relay before; parts marked **new** come from
[better-kumo-app#61](https://github.com/IgorKravtsov/better-kumo-app/pull/61), which this integration needs.
The relay stays generic: it knows about webhooks and earshot, never about Home Assistant or its entities.

## Authentication (existing)

Every `/v1/*` call carries `Authorization: Bearer <secret>`. The secret and the relay address come from the pairing
link on the relay's **Pair a phone…** page:

```
kumowatch://relay?url=<percent-encoded relay URL>&secret=<percent-encoded secret>
```

The config flow accepts this link (or a relay URL plus secret typed separately). Prefer the relay's Tailscale address,
so polling and commands keep working when the laptop is away from home.

The relay URL must point at the relay itself: the integration does not follow redirects (a redirect would resend
the request body, which can carry the webhook secret), so a 3xx answer counts as an error.

**New:** the pairing page has a **Copy link** button next to each QR code.

## Reading state

### `GET /v1/status` (existing, extended)

The relay's own status (snake_case JSON). The config flow reads `uid` from it as the entry's unique id. Polling
reads it on its own, independently of `GET /v1/desktop`, for two fields:

| Field | Type | Meaning |
|---|---|---|
| `idle_seconds` | `integer \| null`, optional | whole seconds since the last local keyboard, mouse or trackpad input on the relay's computer, read on every request. `null` when the relay can't tell (Linux; Windows in session 0); absent from relays older than [better-kumo-app#66](https://github.com/IgorKravtsov/better-kumo-app/pull/66). Never `0` for unknown. |
| `phone_idle_seconds` | `integer \| null`, optional | whole seconds since a paired Better Kumo phone app was last in the foreground (it reports on opening and every 30 s while open), computed on every request. `0` while a Kumospace call is handed off to the phone. `null` until a phone has reported since the relay started (always, with phone apps that don't report); absent from relays older than [better-kumo-app#70](https://github.com/IgorKravtsov/better-kumo-app/pull/70). |

```json
{ "uid": "…", "enabled": true, "online": true, "ntfy": false, "apns": false, "devices": 1, "desktop": true,
  "api": 3, "version": "…", "agents": true, "idle_seconds": 42, "phone_idle_seconds": 7 }
```

`GET /v1/status` answers whether or not Kumospace desktop is connected, so the **At computer** and **Using phone**
entities depend only on it, and are unavailable when it fails:

- **At computer** is unknown when `idle_seconds` is `null`, absent or not an integer from 0 to 2^64 − 1, else on when
  `idle_seconds` is below the **Away after idle** option.
- **Using phone** is unknown when `phone_idle_seconds` is absent or neither `null` nor an integer from 0 to
  2^64 − 1, off when it is `null` (no phone report means not using the phone), else on when it is below the
  **Phone counts as in use for** option.

Neither field is in webhook deliveries (they change every second), so they update only on reads.

### `GET /v1/desktop` (existing, extended)

Returns `DesktopState` (camelCase JSON): `floorId`, `floors`, `self` (`id`, `x`, `y`, `zoneId`, `availability`,
`message`), `zones`, `people`, `audio`, `video`. `availability` is `available`, `away` or `focusing` (or null).

Errors are non-2xx answers:

| Status | When |
|---|---|
| 503 | Kumospace desktop is not connected to the relay, or is connected but not in a space |
| 404 | the relay runs with the desktop bridge disabled |

The integration treats any error the same way: every entity except **At computer**, **Using phone**, **Idle time**
and **Phone idle time** becomes unavailable. Those four follow only `GET /v1/status`; when both reads fail (the relay is unreachable), every entity is
unavailable.

**New fields:**

| Field | Type | Meaning |
|---|---|---|
| `earshot` | `string[]` | ids (`people[].id`) of the people whose audio I currently hear, the same group the Meetings detector uses. Empty when nobody is in earshot. Works whether or not the Meetings module is installed. |
| `inCall` | `bool` | I am in an audio/video conversation (the same signal as `inCall` in the Meetings page tick). |
| `call` | `{ "roomName": string, "peers": string[] } \| null` | the current Daily call; non-null while the desktop is in the call and while the call is handed off to a phone. It never contains the call's `url` or `token`. |

Office events (`better_kumo_event`) are fired only from `event` webhook deliveries, so they need working webhooks;
polling recovers desktop state only. The integration does not call `GET /v1/events`.

## Commands (existing)

| Entity action | Call |
|---|---|
| set status | `POST /v1/desktop/status` `{"availability":"available"\|"away"\|"focusing"}` |
| microphone / camera | `POST /v1/desktop/media` `{"audio":bool}` / `{"video":bool}` |

Each returns the new `DesktopState`; the integration applies it at once and then reads `GET /v1/desktop`, since
the reply carries no revision to order it against other reads.

- `POST /v1/desktop/status`: `message` is optional. Omitted keeps the current status message, `""` clears it.
  The integration never sends it.
- `POST /v1/desktop/media` works only during a call; otherwise it answers 503 `the desktop app is not in a call`.

## Error messages

The integration never reads the body of an error answer, so neither the relay's reason text nor a proxy's error
page reaches error messages, logs or diagnostics. Each message is built from the action, the HTTP status and fixed
text, for example `Turning the microphone on failed: Kumospace desktop is not in a call (HTTP 503)`:

| Status | Desktop reads and status | Microphone / camera | Other endpoints |
|---|---|---|---|
| 503 | Kumospace desktop is not connected to the relay, or not in a space | Kumospace desktop is not in a call | the relay is temporarily unavailable |
| 404 | the relay runs with the desktop bridge disabled | same | the relay does not know this endpoint; update the relay |
| 401 | wrong relay secret | same | same |
| 400 | the relay rejected the request as invalid | same | same |
| 3xx | the relay answered with a redirect; use the relay's own address | same | same |
| other | the relay answered with an error | same | same |

Replies that are not UTF-8, not JSON or not the expected shape get a fixed `unexpected reply from the relay`
message. Connection failures keep aiohttp's text (host and OS error); TLS errors, which can quote the peer
certificate, and other aiohttp errors, which can quote a malformed reply, show only the error type.

## Webhooks (new)

### Register: `PUT /v1/webhooks/{id}`

`id` is chosen by the client; the integration uses its config entry id, so re-registering is idempotent.

```json
{
  "urls": ["http://homeassistant.local:8123/api/webhook/<ha-webhook-id>",
           "https://ha.example.com/api/webhook/<ha-webhook-id>"],
  "secret": "<64 hex chars, generated by the client>"
}
```

Answers `{"id": "…", "urls": […]}`. Limits (400 otherwise):

- `id`: 1–64 characters, `[A-Za-z0-9_-]`.
- `urls`: 1–8 `http://` or `https://` URLs with a host.
- `secret`: 16–256 bytes of UTF-8. The HMAC key is those bytes, not the hex-decoded value.

Behaviour:

- `urls` is ordered. The relay delivers to the first URL that answers 2xx, remembers the one that worked and starts
  from it next time, and falls back through the list on failure.
- The relay stores registrations on disk so they survive a restart. The integration also re-registers every
  10 minutes and on startup. Re-registering an unchanged body changes nothing.
- `DELETE /v1/webhooks/{id}` answers 204, also for an unknown id. The integration calls it when the config entry is
  removed.
- `GET /v1/webhooks` answers `[{"id": "…", "urls": […]}]` (never secrets), for debugging.

### Delivery

`POST <url>` with `Content-Type: application/json` and

```
X-Kumo-Signature: sha256=<hex HMAC-SHA256 of the raw body, keyed with the secret's UTF-8 bytes>
```

Body:

```json
{ "boot": "<relay boot id>", "seq": 42, "kind": "desktop", "desktop": { …DesktopState with the new fields… } }
{ "boot": "<relay boot id>", "seq": 43, "kind": "event",   "event":   { …LoggedEvent… } }
{ "boot": "<relay boot id>", "seq": 44, "kind": "disconnected" }
```

- `seq` is one counter per relay boot (`0 ≤ seq ≤ 2^64−1`), and a new `boot` restarts it. Every body goes to every
  webhook with the same `seq`, so a gap means bodies this receiver did not get (dropped from a full queue or refused
  by every URL), and a webhook registered later starts above 1. The integration drops bodies with a bad signature
  or envelope and uses `(boot, seq)` only to fire a redelivered `event` once.
- `desktop` and `disconnected` are signals for this integration: it ignores the snapshot in the body and reads
  `GET /v1/desktop` right away (one extra LAN round trip; bodies that arrive while a read is in flight cause one
  more read after it). Bodies and HTTP replies share no revision, so applying a delayed body could undo a newer
  read. Availability comes from that read: a failed read makes entities unavailable. If Home Assistant cannot reach
  the relay, pushes do not help, since polling fails too and entities stay unavailable.
- `desktop` is sent when the snapshot changes. The relay reads the state 250 ms after an office frame, a
  (dis)connect or a registration change, and otherwise about every 1.25 s, since Daily changes such as earshot and
  the call send no office frame. Position-only changes (`x`/`y` of `self` or `people`) are not sent; any other
  change is.
- `disconnected` is sent when the state can no longer be read after a `desktop` was sent: Kumospace desktop went
  away, or runs but is not in a space (the same cases where `GET /v1/desktop` answers 503).
- At relay start and after a new or changed registration, the relay sends the current `desktop` or
  `disconnected` on its next read, to every registered webhook.
- `event` is sent for every entry the relay's event log records. See [Events](#events) for its shape.
- Delivery is fire-and-forget: 3 s timeout per URL attempt, redirects are not followed (the webhook URL must be the
  final address), and each webhook has a queue of 64 bodies; when it is full, new bodies are dropped. Missed
  `desktop` deliveries are covered by polling; missed `event` deliveries are lost.

### Events

`event` is a `LoggedEvent`: `seq` (the event log's own counter), `kind`, `at`, `person` `{user_id, name}`, and,
depending on the kind, `room` `{id, name}`, `floor` `{id, name}`, `status`, `chat` and `text`. Empty strings and
absent places are omitted.

Office kinds (existing): `nudge`, `knock`, `entered_room`, `left_room`, `joined_floor`, `online`, `offline`,
`status`, `nearby_chat`.

**New** (better-kumo-app branch `relay-chat-events`): kind `message`, sent for every
chat message in every channel (direct, group, public, threads) except my own.

```json
{ "seq": 9, "kind": "message", "at": 1764240000,
  "person": { "user_id": "u2", "name": "Alice" },
  "chat": "Design", "text": "Lunch?",
  "channel": "<channel url>", "channel_type": "group", "mentions_me": true }
```

| Field | Type | Meaning |
|---|---|---|
| `person` | `{user_id, name}` | the sender |
| `chat` | `string` | the channel title |
| `text` | `string` | the message text |
| `channel` | `string`, optional | the channel url |
| `channel_type` | `string`, optional | `direct`, `group`, `channel` (a public channel) or `other` |
| `mentions_me` | `bool`, optional | the message mentions me; omitted when false |
| `thread` | `bool`, optional | the message is a thread reply; omitted when false |

The integration fires every `event` delivery as `better_kumo_event` unchanged. It also shows `message` on the
Messages entity and the other kinds on the Office events entity, where kinds it does not know become `other`; the
relay may add kinds without breaking it.

## Polling

The integration polls `GET /v1/desktop` and `GET /v1/status` independently on a configurable interval (1–3600 s,
default 30 s) whether or not webhooks work, and reads `GET /v1/desktop` again after every `desktop` or
`disconnected` delivery. If `GET /v1/desktop` fails, the Kumospace entities become unavailable; if `GET /v1/status`
fails, **At computer**, **Using phone** and the idle time sensors do. Neither read waits for the other, and a command reply touches only the
Kumospace entities.
