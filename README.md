# Better Kumo for Home Assistant

A Home Assistant custom integration (HACS) that shows your [Kumospace](https://www.kumospace.com) presence in Home
Assistant and controls it, through the [Better Kumo](https://github.com/IgorKravtsov/better-kumo-app) relay on the
computer where Kumospace runs.

Status: in development. The relay contract is in [docs/relay-api.md](docs/relay-api.md).

Planned:

- status (`sensor` + `select`), current room, microphone and camera switches;
- `binary_sensor` "in a call" and "someone in earshot", plus the number and names of people in earshot;
- `better_kumo_event` for office events (nudges, knocks, …);
- updates pushed by webhook from the relay, with polling as a fallback;
- a blueprint that changes a light while you are talking.
