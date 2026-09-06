# Local HTTP API

The dashboard is a client of this API; anything else on the machine can use it too. Base URL: `http://127.0.0.1:6733` (port in Settings). All bodies and responses are JSON. Errors are `{"error": "..."}` with a 4xx/5xx status.

## Events

```
POST /api/events
{"type": "build.failed", "data": {"name": "web"}, "source": "ci"}
```

`type` is any dotted name (≤ 80 chars); the catalog in `GET /api/state` lists the ones with labels. `data` is an object rules can filter on. If `webhook_token` is set in Settings, send `Authorization: Bearer <token>`. Returns the stored event.

## Access

Every request needs a loopback `Host` header (`127.0.0.1:<port>` or `localhost:<port>`), and an `Origin`, if the client sends one, pointing at the same place. Anything else gets `403 {"error": "forbidden origin"}`: without that check any web page the user has open could drive the daemon. `POST /api/events` is the exception — it may come from anywhere as long as it carries the bearer token, which is how CI reaches it through a tunnel.

## State

```
GET /api/state
```

Everything the dashboard shows: `version`, `uptime_s`, `paused`, `scanning`, `onboarded`, `settings`, `devices[]` (with current `color` and per-zone `zone_colors`), `integrations[]`, `sessions[]` (live agent sessions: `id`, `agent`, `status`, `slot`, `cwd`, `started`), `rules[]`, `activity[]` (recent events and the rules they fired), `messages[]`, `catalog`, `effects`.

## Devices

```
POST  /api/scan                                    rescan; returns devices
POST  /api/devices/<id>/test   {"effect": "flash", "color": [0,255,0], "count": 2, "duration": 1.5, "message": "...", "sound": "success"}
PATCH /api/devices/<id>        {"enabled": false, "name": "Desk lamp"}
POST  /api/adapters/<module>/<action>   adapter setup step, e.g. /api/adapters/hue/pair {"bridge": "192.168.1.5"}
POST  /api/pause               {"paused": true}      release all devices to their firmware
PATCH /api/devices/<id>        {"brightness": 0.5}   per-device dimmer, applied to every write
DELETE /api/devices/<id>                             forget a disconnected device's saved settings
DELETE /api/activity                                 clear the feed
```

## Rules

```
GET    /api/rules
PUT    /api/rules              [rule, ...]           replace all (order matters)
POST   /api/rules              rule                  create or update (by id)
DELETE /api/rules/<id>
POST   /api/rules/preview      [action, ...]         run actions now without saving
```

A rule:

```json
{
  "id": "3f9a1c2b", "name": "Task finished", "enabled": true,
  "when": "agent.finished", "match": {"agent": "claude"},
  "actions": [
    {"device": "*", "effect": "flash", "color": [0, 255, 0], "count": 2, "duration": 1.2, "brightness": 1.0},
    {"device": "notification", "effect": "notify", "message": "Done — {agent}"}
  ]
}
```

`when` accepts fnmatch wildcards (`build.*`). `device` is a device id or `*` for every device that supports the effect. Effects: `set`, `off`, `flash`, `pulse`, `wave`, `brightness_pulse`, `brightness_blink`, `notify`, `sound`, `sessions`. `message` may use `{key}` placeholders from the event data.

`sessions` (for `agents.sessions` events) paints one zone per live agent session: zone N shows the status color of the session in slot N + `offset` (`palette`: `{"running": [..], "input": [..], "done": [..]}`, empty slots dark). A session keeps its slot until it ends; new ones take the lowest free slot. `agent` limits it to one agent's sessions (re-ranked, so `"claude"` on the keyboard and `"codex"` on the light bar split the hardware between agents); `per_zone: false` paints the whole device with the folded status instead. Devices without zones always show the folded status.

## Live updates

```
GET /api/stream        text/event-stream
```

Sends `data: <revision>` whenever anything in `GET /api/state` would look different, and a comment line every 15 s to keep the connection alive. The dashboard refetches the state on each message instead of polling; if the stream cannot be opened it falls back to polling every 2 s.

## Agent sessions

```
PATCH /api/sessions/<id>   {"slot": 2}                which zone this tab owns; the tab already there swaps into its place
PATCH /api/sessions/<id>   {"label": "API refactor"}  name a tab so its zone means something
PUT   /api/sessions/order  ["id", "id", ...]          lay these tabs over the zones they already occupy, in this order
DELETE /api/sessions/<id>                             forget a tab that closed without a SessionEnd hook
```

A slot or label set this way is pinned in `slots.json` under both the session id and its working directory, so it survives the tab, the daemon, and the next tab you open in that project. Pins are forgotten after a week.

## Presets

```
GET    /api/presets
POST   /api/presets          {"name": "Success", "actions": [action, ...]}
DELETE /api/presets/<id>
```

A preset is a named bundle of actions the rule editor can copy into a new automation.

## Integrations & settings

```
POST  /api/integrations/<id>/connect        e.g. install Claude Code hooks; returns {"message": ...}
POST  /api/integrations/<id>/disconnect
PATCH /api/integrations/<id>                {"repos": "owner/repo"}   integration options
PUT   /api/settings                          partial settings object; returns the full settings
POST  /api/onboarded                         mark first-run setup as done
DELETE /api/sessions/<id>                    forget an agent session whose tab closed without a SessionEnd hook
```

Settings keys: `port`, `autostart`, `start_minimized`, `rescan_interval_s`, `reduce_flashing`, `webhook_token`, `launch_openrgb`, `log_events`, `quiet_hours`.

`quiet_hours` is `{"enabled": bool, "from": "23:00", "to": "07:00", "mode": "no_flash" | "dark"}` in local time; a window that wraps midnight is normal. `no_flash` keeps status colours but plays no transient effect; `dark` also writes black to every light. Notifications and sounds are left alone — that is the operating system's own do-not-disturb. A value the daemon could not start with (`port` outside 1024–65535, a negative `rescan_interval_s`) is refused with `400` rather than silently clamped.

## Updates

```
GET  /api/update      {"current", "latest", "url", "asset", "frozen", "available"}
POST /api/update      download the release binary and restart; returns {"message": ...}
```

Both reach out to the GitHub releases API, and only when called — nothing checks for updates on its own. `POST` only replaces the binary for a frozen build; a source install is told to use its package manager. The download is refused unless its SHA-256 matches the `SHA256SUMS` asset published with the release, so a swapped binary cannot be installed.
