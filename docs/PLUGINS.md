# Writing plugins

Lumen has two plugin types. Both are plain Python modules; both can live inside this repository (`src/lumen/devices/`, `src/lumen/integrations/`) or in your own package registered through an entry point.

## Device adapters

An adapter turns hardware (or a system feature) into one or more `Device` objects.

```python
# src/lumen/devices/acme_lamp.py
"""Acme desk lamp over its local HTTP API."""
from lumen.core.devices import BRIGHTNESS, COLOR, Device


class AcmeLamp(Device):
    def __init__(self, ip: str):
        super().__init__(
            id=f"acme-{ip}",                  # stable across restarts
            name="Acme lamp", kind="light", vendor="Acme",
            capabilities=frozenset({COLOR, BRIGHTNESS}),
            details={"ip": ip, "connection": "Wi-Fi"},
        )
        self.ip = ip
        self.max_fps = 10                     # network devices: cap the write rate

    def set_color(self, rgb):                 # required for COLOR
        ...

    def set_brightness(self, level):          # required for BRIGHTNESS, level 0..1
        ...


def discover(settings: dict | None = None) -> list[Device]:
    """Return every Acme lamp on the network. Never raise: no lamps = []."""
    try:
        return [AcmeLamp(ip) for ip in find_lamps(timeout=1.5)]
    except OSError:
        return []
```

Then add `"lumen.devices.acme_lamp"` to `BUILTIN_ADAPTERS` in `src/lumen/core/devices.py`, or in your own package declare an entry point:

```toml
[project.entry-points."lumen.devices"]
acme = "lumen_acme.adapter"
```

### The Device contract

| Field / method | Meaning |
|---|---|
| `id` | Stable identifier; rules and settings reference it. |
| `kind` | One of `keyboard, lightbar, strip, light, mouse, headset, screen, notification, sound, other` (icon + sort order). |
| `capabilities` | Subset of `color`, `brightness`, `zones`, `notify`, `sound`. The rule builder derives the effect menu from these. |
| `set_color(rgb)` | `color`. `(0,0,0)` means off. |
| `set_zones([rgb, ...])` | `zones`; `zone_count` tells the player how many. Enables `wave`. |
| `set_brightness(level)` | `brightness`, 0..1. |
| `notify(title, message)` / `play_sound(name, volume=1.0)` | `notify` / `sound`. Run in a worker thread; may block. `volume` is 0.05–1, the device's slider on the Devices page; an adapter that takes the name alone still works. |
| `remember()` / `restore(state)` | Optional. Snapshot the device before a transient effect when no base color is set, and put it back after (Hue does this). |
| `refresh_every_s` | Optional. Re-send the base color periodically for volatile direct-mode hardware. |
| `max_fps` | Optional write-rate cap. |
| `ambient` | Default `True`. Set `False` if wildcard *persistent* colors should skip the device (the screen glow does). |
| `close()` | Release handles. Called on rescan, pause and shutdown. |
| `ACTIONS` (module level) | Optional `{"name": fn(options, params) -> (message, new_options)}` for setup steps the dashboard can call (`POST /api/adapters/<module>/<name>`). Hue uses it for pairing. |

Raise `OSError` (or any exception) from a write when the device is gone: the player marks it disconnected and the next scan reconnects.

`discover()` receives the current settings dict (`settings["devices"][<adapter>]` holds anything your `ACTIONS` saved) — useful for credentials and manual addresses.

## Integrations

An integration is a source of events. Subclass `Integration`, emit `Event`s, and expose the class as `INTEGRATION`.

```python
# src/lumen/integrations/jenkins.py
"""Jenkins builds, polled."""
import threading
from lumen.core.events import Event
from lumen.core.integrations import Integration


class Jenkins(Integration):
    id = "jenkins"
    name = "Jenkins"
    description = "Build results from a Jenkins server."
    events = ("build.succeeded", "build.failed")
    option_fields = {"url": {"label": "Server URL", "type": "text", "placeholder": "https://ci.example.com"}}
    docs = "Needs an API token; see …"

    def start(self):
        self._stop = threading.Event()
        threading.Thread(target=self._loop, daemon=True).start()

    def stop(self):
        self._stop.set()

    def _loop(self):
        while not self._stop.wait(60):
            for build in new_finished_builds(self.options.get("url")):
                self.emit(Event("build.succeeded" if build.ok else "build.failed", "jenkins", {"name": build.job}))

    def status(self):
        return {"connected": bool(self.options.get("url")), "detail": self.options.get("url", "not configured")}


INTEGRATION = Jenkins
```

Register it in `BUILTIN_INTEGRATIONS` (`src/lumen/core/integrations.py`) or via the `lumen.integrations` entry point (`name = "package.module:INTEGRATION"`).

- `option_fields` become inputs on the Integrations page; saved values arrive in `self.options` (and `set_options()` on change).
- `can_connect = True` plus `connect()` / `disconnect()` gives you a one-click setup button (the agents use it to install hooks).
- `self.settings` is a live view of the global settings (port, token…).
- Use a type from the catalog in `core/events.py` when one fits so rules get nice labels; any dotted type is accepted.

## Testing

Fake the I/O, test the logic. `tests/test_lightbar.py` fakes the HID controller, `tests/test_api.py` runs the whole engine with fake devices. Adapters that need real hardware should still have a test for their pure functions (report layout, parsing, color math).
