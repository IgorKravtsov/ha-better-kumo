# Better Kumo for Home Assistant

A Home Assistant custom integration (HACS) that shows your [Kumospace](https://www.kumospace.com) presence in Home
Assistant and controls it, through the [Better Kumo](https://github.com/IgorKravtsov/better-kumo-app) relay on the
computer where Kumospace runs. The relay contract is in [docs/relay-api.md](docs/relay-api.md).

## What you get

One device, **Kumospace**, per relay:

| Entity | What it does |
|---|---|
| `sensor` Status | `available`, `away` or `focusing` |
| `select` Status | changes your status (keeps your status message; an older relay clears it) |
| `sensor` Room | name of the room you are in, empty outside rooms |
| `switch` Microphone, Camera | turn your mic and camera on or off, during a call only |
| `binary_sensor` In a call | on while you are in an audio/video conversation |
| `binary_sensor` Someone in earshot | on while you hear someone |
| `sensor` People in earshot | how many people you hear; attribute `names` lists them |
| `event` Messages | a chat message from someone else, in any channel (direct, group, public, threads) |
| `event` Office events | nudges, knocks, people entering or leaving rooms, joining a floor, going online or offline, changing status, nearby chat |

## Events

Every event the relay pushes (chat messages and office events) is fired on the event bus as `better_kumo_event`,
with the relay's event fields plus `config_entry_id`, and is shown by one of two event entities. Events arrive only
through the webhook, each `(boot, seq)` once; polling recovers state, not missed events, and never triggers the
event entities.

- **Messages** (`event.kumospace_messages`), event type `message`. Attributes: `sender`, `sender_id`, `chat` (the
  channel title), `text`, `channel` (the channel url), `channel_type` (as the relay sends it: `direct`,
  `group`, `channel` for a public channel, or `other`), `mentions_me`, `thread` and `at`. Filter on the attributes in a condition, for example
  `mentions_me` is `true` or `channel_type` is `direct`. Your own messages are not sent by the relay.
- **Office events** (`event.kumospace_office_events`), one event type per kind: `nudge`, `knock`,
  `entered_room`, `left_room`, `joined_floor`, `online`, `offline`, `status`, `nearby_chat`. A kind this version
  does not know (from a newer relay) shows as `other`. Attributes: `kind`, `person`, `person_id`, `room`, `floor`,
  `status`, `chat`, `text` and `at`, each only when the event has it.

Attributes are capped so the recorder keeps them (it drops state attributes over 16 KiB): `text` to 1024
characters and every other string to 255, cut ones ending in `…`. `better_kumo_event` keeps the full text.

The event entities are unavailable when the other entities are. An event that arrives meanwhile is kept and shown
when they come back, so an automation that triggers on the entity's state should ignore changes from and to
`unavailable` (and to `unknown`). The last event is restored after a restart.

Example: notify on a direct message or a mention.

```yaml
triggers:
  - trigger: state
    entity_id: event.kumospace_messages
    not_from: [unavailable]
    not_to: [unavailable, unknown]
conditions:
  - condition: template
    value_template: >-
      {{ trigger.to_state.attributes.mentions_me
         or trigger.to_state.attributes.channel_type == 'direct' }}
actions:
  - action: notify.notify
    data:
      title: "{{ trigger.to_state.attributes.sender }}"
      message: "{{ trigger.to_state.attributes.text }}"
```

## Availability

When the relay is unreachable, or Kumospace desktop is not connected to it, every entity is unavailable.
`In a call` and the earshot entities stay `unknown` with a relay older than these fields. Webhooks, earshot, the
call fields and keeping the status message need a relay with
[better-kumo-app#61](https://github.com/IgorKravtsov/better-kumo-app/pull/61).

## Install

1. HACS → ⋮ → **Custom repositories** → add `https://github.com/IgorKravtsov/ha-better-kumo`, category
   **Integration**.
2. Install **Better Kumo** and restart Home Assistant.

## Pair

1. On the computer with the relay, open its **Pair a phone…** page and copy the pairing link
   (`kumowatch://relay?url=…&secret=…`).
2. Settings → Devices & services → **Add integration** → **Better Kumo**, and paste the link. Or leave the link
   empty and type the relay URL (for example `http://kumo-mac.local:8787`) and its secret.

Prefer the relay's Tailscale address, so Home Assistant keeps reaching the relay when the computer is away from
home. Pairing again with a new address or secret updates the existing entry.

## Options

- **Poll interval**: seconds between reads of the relay state, 1–3600, default 30.
- **Home Assistant URL for webhooks**: optional, for example `https://ha.example.com`. The relay tries it first.

## Push and polling

Home Assistant polls `GET /v1/desktop` at the poll interval, always. On top of that it registers a webhook in the
relay, so changes show up within a fraction of a second:

- A state delivery (`desktop` or `disconnected`) only tells Home Assistant that the state changed. It ignores the
  snapshot in the body and reads `GET /v1/desktop` right away, one extra round trip to the relay; bursts of
  deliveries share one read. The read decides availability: if it fails, entities become unavailable. So a push
  helps only while Home Assistant can reach the relay; when it cannot, polling fails too and entities are
  unavailable. After every command the integration also reads the state again.

- On startup and every 10 minutes the integration sends the relay an ordered list of URLs for its webhook: the
  override from the options (if set), the internal URL of Home Assistant (or its detected LAN address when no
  internal URL is set), then its external URL (if any). The relay uses the first that answers and falls back
  through the rest, so a laptop away from home can still push through the external URL. The relay does not
  follow redirects, so each URL must reach Home Assistant directly (for example `https://`, not an `http://`
  address that redirects to it).
- Every delivery is signed with a per-entry secret (HMAC-SHA256). Deliveries with a bad signature are dropped. Because of the signature the webhook accepts requests from outside
  the local network, which the external URL and Tailscale addresses (not "local" to Home Assistant) need.
- If registration fails (for example an older relay without webhooks), the integration keeps working on polling
  and logs one warning. The webhook URLs are in the integration's diagnostics.
- Removing the integration removes its webhook from the relay.

## Blueprint: light while talking

[![Import blueprint](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2FIgorKravtsov%2Fha-better-kumo%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fbetter_kumo%2Ftalking_light.yaml)

Or import `https://github.com/IgorKravtsov/ha-better-kumo/blob/main/blueprints/automation/better_kumo/talking_light.yaml`
under Settings → Automations & scenes → Blueprints → **Import blueprint**.

Inputs: a light, the color to use while talking, the restore delay (default 30 s), and the **In a call** and
**Someone in earshot** sensors. When either sensor turns on, the automation saves the light's current state in a
temporary scene and sets the color. When both have been off for the delay, it restores the light from that scene.

## Development

```sh
uv venv -p 3.14 .venv
uv pip install -p .venv -r requirements_test.txt
.venv/bin/pytest
.venv/bin/ruff check . && .venv/bin/ruff format --check .
```
