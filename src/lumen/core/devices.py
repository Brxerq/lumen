"""Device model and the adapter registry.

A *device* is anything that can react to an event: an RGB keyboard, a light
bar, a smart bulb, the screen, the OS notification center, the speaker. Each
one advertises *capabilities*, and the rule builder only offers effects a
device can actually perform.

An *adapter* is a module in `lumen.devices` (or a third-party package exposing
the `lumen.devices` entry point) with one function:

    def discover() -> list[Device]

Adapters never raise out of discover(): a missing driver or an unplugged
device just means an empty list.
"""

from __future__ import annotations

import importlib
import re
import threading
from dataclasses import dataclass, field

RGB = tuple[int, int, int]

# A device id travels a long way: it is a config key, a path segment in
# /api/devices/<id>, and an argument in the dashboard's inline click handlers.
# Some of them are named by the network — a Govee light announces its own id
# over multicast — so the charset is pinned here, once, for every adapter
# including third-party ones, rather than trusted at each of those three ends.
_UNSAFE_ID = re.compile(r"[^a-z0-9_-]+")

# Capabilities. Effects map onto these; see core/effects.py.
COLOR = "color"            # set_color(rgb)
BRIGHTNESS = "brightness"  # set_brightness(0..1), for backlights without color
ZONES = "zones"            # set_zones([rgb, ...]) — multi-zone strip/keyboard, enables wave
NOTIFY = "notify"          # notify(title, message)
SOUND = "sound"            # play_sound(name, volume)

# Kinds, for icons and grouping in the UI.
KINDS = ("keyboard", "lightbar", "strip", "light", "mouse", "headset", "screen", "notification", "sound", "other")


@dataclass
class Device:
    """Base class. Adapters subclass it and implement the methods matching
    their capabilities. Everything else has a safe default."""

    id: str
    name: str
    kind: str = "other"
    vendor: str = ""
    capabilities: frozenset = frozenset()
    connected: bool = True
    zone_count: int = 1
    # Direct-mode hardware often forgets its color (firmware/vendor software
    # repaints). If set, the effect player re-sends the base color this often.
    refresh_every_s: float | None = None
    # Free-form facts for the Devices page (model, address, protocol...).
    details: dict = field(default_factory=dict)
    # Ambient devices take part in wildcard ("all devices") persistent colors.
    # The screen glow doesn't: a permanent border is intrusive, so it only
    # plays transient effects unless a rule targets it by name.
    ambient: bool = True

    def __post_init__(self) -> None:
        self.id = _UNSAFE_ID.sub("-", str(self.id).lower()).strip("-") or "device"

    # --- capability methods ------------------------------------------------
    def set_color(self, rgb: RGB) -> None:
        raise NotImplementedError

    def set_brightness(self, level: float) -> None:
        raise NotImplementedError

    def set_zones(self, colors: list[RGB]) -> None:
        self.set_color(colors[0])

    def notify(self, title: str, message: str) -> None:
        raise NotImplementedError

    def play_sound(self, name: str, volume: float = 1.0) -> None:
        raise NotImplementedError

    # Optional. Hardware whose state we cannot read back (a Hue bulb the user
    # set by hand) can hand the player an opaque token before a transient effect
    # and take it back afterwards, so an effect leaves no trace.
    def remember(self) -> object | None:
        return None

    def restore(self, saved: object) -> None:
        pass

    def off(self) -> None:
        if COLOR in self.capabilities:
            self.set_color((0, 0, 0))
        elif BRIGHTNESS in self.capabilities:
            self.set_brightness(0.0)

    def close(self) -> None:
        pass

    # --- description ---------------------------------------------------------
    def supports(self, capability: str) -> bool:
        return capability in self.capabilities

    def to_dict(self) -> dict:
        return {
            "id": self.id, "name": self.name, "kind": self.kind, "vendor": self.vendor,
            "capabilities": sorted(self.capabilities), "connected": self.connected,
            "zones": self.zone_count, "ambient": self.ambient, "details": dict(self.details),
        }


BUILTIN_ADAPTERS = [
    "lumen.devices.asus_aura",
    "lumen.devices.openrgb",
    "lumen.devices.linux_backlight",
    "lumen.devices.mac_backlight",
    "lumen.devices.govee",
    "lumen.devices.hue",
    "lumen.devices.screen",
    "lumen.devices.notch",
    "lumen.devices.notification",
    "lumen.devices.sound",
]


def adapter_modules() -> list:
    """Built-in adapters plus any installed package advertising `lumen.devices`."""
    mods = []
    for name in BUILTIN_ADAPTERS:
        try:
            mods.append(importlib.import_module(name))
        except Exception as e:
            print(f"devices: adapter {name} failed to import: {type(e).__name__}: {e}", flush=True)
    try:
        from importlib.metadata import entry_points
        for ep in entry_points(group="lumen.devices"):
            try:
                mods.append(ep.load())
            except Exception as e:
                print(f"devices: plugin {ep.name} failed to load: {type(e).__name__}: {e}", flush=True)
    except Exception:
        pass
    return mods


def discover_all(settings: dict | None = None) -> list[Device]:
    """Run every adapter's discover(). Slow adapters (network scans) run in
    parallel; a broken one is logged and skipped."""
    settings = settings or {}
    found: list[Device] = []
    lock = threading.Lock()

    def run(mod):
        try:
            devices = mod.discover(settings) if _takes_settings(mod.discover) else mod.discover()
        except Exception as e:
            print(f"devices: {mod.__name__}.discover failed: {type(e).__name__}: {e}", flush=True)
            return
        with lock:
            found.extend(devices)

    threads = [threading.Thread(target=run, args=(m,), daemon=True) for m in adapter_modules() if hasattr(m, "discover")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)
    seen: set[str] = set()
    unique = []
    for d in found:
        if d.id not in seen:
            seen.add(d.id)
            unique.append(d)
    # real hardware first, the always-there built-ins (screen, notifications, sound) last
    rank = {k: i for i, k in enumerate(KINDS)}
    return sorted(unique, key=lambda d: (rank.get(d.kind, len(KINDS)), d.name.lower()))


def _takes_settings(fn) -> bool:
    import inspect
    try:
        return len(inspect.signature(fn).parameters) >= 1
    except (TypeError, ValueError):
        return False
