# Architecture

Lumen is one Python process: a daemon with a tray icon, an HTTP server on `127.0.0.1` for the dashboard, and an engine that turns events into device writes.

```
┌─────────────────────────────────────────────────────────────────────────┐
│ lumen daemon                                                            │
│                                                                         │
│  integrations/            core/                        devices/         │
│  ┌──────────────┐ emit  ┌──────────┐  match  ┌───────┐ write ┌────────┐ │
│  │ claude_code  │──────▶│ EventBus │────────▶│ Rules │──────▶│asus_aura│ │
│  │ codex        │       └──────────┘         └───────┘       │openrgb │ │
│  │ github       │            │                   │           │hue     │ │
│  │ terminal     │            ▼                   ▼           │govee   │ │
│  │ webhook      │      activity log        EffectPlayer ────▶│screen  │ │
│  └──────────────┘                          (30 fps thread)   │notify  │ │
│         ▲                                                    │sound   │ │
│         │ hook files / polling                               └────────┘ │
│                                                                         │
│  server/api.py  ◀── dashboard (server/ui, vanilla JS) ── app/tray.py    │
└─────────────────────────────────────────────────────────────────────────┘
```

## Modules

| Path | Role |
|---|---|
| `core/events.py` | `Event` (type, source, data, ts), `EventBus` (sync fan-out + history), the event catalog for the UI. |
| `core/devices.py` | `Device` base class, capability constants, adapter registry (`BUILTIN_ADAPTERS` + `lumen.devices` entry points), parallel `discover_all()`. |
| `core/rules.py` | `Rule` / `Action` dataclasses, the effect table, matching (fnmatch on type + data equality), default rules. |
| `core/effects.py` | `EffectPlayer`: base color per device + transient effects; envelopes (flash/pulse/wave); degradation to brightness; write-on-change and per-device rate caps. |
| `core/config.py` | `config.json` (settings, rules, device and integration options), atomic writes, tolerant reads. |
| `core/integrations.py` | `Integration` base class and registry. |
| `core/engine.py` | Wires it together: discovery + rescan, rule evaluation on every event, replaying persistent colors onto newly found devices, the dashboard snapshot. |
| `devices/*` | One adapter per hardware family. See `docs/PLUGINS.md`. |
| `integrations/*` | One event source each. `agent_sessions.py` is shared by Claude Code and Codex (hook command, session files, folding). |
| `server/api.py` | Standard-library `ThreadingHTTPServer`: static UI + JSON API. |
| `server/ui/` | The dashboard: one HTML file, one CSS file, one JS file, no build step. Live updates over `/api/stream`, with polling as a fallback. |
| `app/tray.py` | Tray icon (pystray), single-instance mutex/lock, log redirection for windowed builds, `run_app()`. |
| `app/autostart.py` | Start at login: HKCU Run key, LaunchAgent plist, XDG autostart. |
| `cli.py` | `lumen` command; `lumen hook` is a stdlib-only fast path. |
| `paths.py` | Per-platform data directory (`LUMEN_HOME` overrides). |

## Key decisions

**Events, not device commands, are the integration boundary.** An integration knows nothing about hardware; a device knows nothing about agents. Rules are the only place the two meet, and users own the rules.

**Two color layers per device.** A *base* color (from persistent `set`/`off` effects) is what the device rests at; a *transient* effect plays on top and then the base returns. This is what lets "amber while working" and "flash green when done" coexist without rules fighting.

**Degradation over exclusion.** `flash` on a device without color becomes a brightness blink; `wave` without zones becomes a pulse; a wildcard action simply skips devices that can't do anything meaningful. The UI never offers an effect a device can't perform.

**Agents are read two ways.** Hooks give instant, precise state (including "waiting for you"), but they are async and can miss a `Stop`. The agent's own record (Claude transcript, Codex rollout) is authoritative for open/closed, so a stuck hook file can never leave a light on. See `integrations/agent_sessions.py::fold`.

**The hook path is tiny.** `lumen hook` writes one small JSON file and exits; it imports nothing outside the standard library. The daemon folds the files on its own schedule.

**Standard library first.** HTTP server, JSON, sqlite, sockets, tkinter (screen glow), winsound — all stdlib. Third-party packages are confined to adapters (`hidapi`, `openrgb-python`) and the tray (`pystray`, `pillow`), and every one is optional at import time.

**Loopback only, one token.** The API binds to `127.0.0.1`. Every request must also carry a loopback `Host` header and, if it has one, a loopback `Origin` — otherwise a page in the user's browser could drive the daemon, and a hostile DNS name could resolve to 127.0.0.1. The only endpoint meant for other machines is `POST /api/events`, which is exempt from that check when it presents the bearer token from Settings; expose it through a tunnel or reverse proxy if you need remote CI to reach it.

## Data

Everything lives in one directory (`lumen.paths.data_dir()`):

```
config.json      settings, rules, device/integration options
sessions/        <session_id>.json written by `lumen hook`
slots.json       pinned zone and name per agent session and project
lumen.log        daemon output when running windowed
```

## Threads

- main: tray icon (pystray requires it), or a sleep loop with `--no-tray`
- `lumen-http`: HTTP server (thread per request)
- `lumen-effects`: the effect player tick
- `lumen-rescan`: periodic discovery
- one thread per polling integration (`lumen-claude`, `lumen-codex`, `lumen-github`)
- short-lived workers for notifications and sounds

All device writes go through the player thread (or a request thread for tests/previews) under the player's lock; adapters that share a handle (ASUS keyboard + light bar) serialize internally.
