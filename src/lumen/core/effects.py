"""The effect player: turns actions into device writes over time.

Two layers per device:

* a *base* color — what the device shows when nothing else is happening,
  written by the persistent `set` effect (e.g. "amber while an agent works");
* a *transient* effect — flash / pulse / wave — that plays on top for a few
  seconds, then the base comes back.

Effects degrade to what a device can do: a `flash` on a backlight without
color becomes a brightness blink; on a notification "device" it is ignored
unless asked for explicitly. One thread drives all devices at a fixed tick and
only writes when a color actually changes, so idle costs nothing.
"""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from lumen.core.devices import BRIGHTNESS, COLOR, ZONES, Device
from lumen.core.events import CATALOG, Event
from lumen.core.rules import EFFECTS, Action

RGB = tuple[int, int, int]

# What a color effect becomes on a brightness-only device.
_DEGRADE = {"set": "brightness_set", "flash": "brightness_blink", "pulse": "brightness_pulse",
            "wave": "brightness_pulse", "off": "brightness_off"}


@dataclass
class Transient:
    effect: str
    color: RGB
    start: float
    duration: float
    count: int
    brightness: float
    saved: object = None  # device.remember() result, restored when done

    def phase(self, now: float) -> float | None:
        """0..count while playing, None when over."""
        t = (now - self.start) / self.duration
        return None if t >= 1.0 else t * self.count


def within(window: dict, now: time.struct_time | None = None) -> bool:
    """Is the local clock inside a quiet-hours window? Windows normally wrap
    midnight (23:00 to 07:00), so "from > to" means "or"."""
    if not window.get("enabled"):
        return False
    now = now or time.localtime()
    minutes = now.tm_hour * 60 + now.tm_min
    try:
        start = _minutes(window["from"])
        end = _minutes(window["to"])
    except (KeyError, ValueError):
        return False
    if start == end:
        return False
    return start <= minutes < end if start < end else (minutes >= start or minutes < end)


def _minutes(hhmm: str) -> int:
    hours, mins = str(hhmm).split(":")
    return int(hours) * 60 + int(mins)


def scale(rgb: RGB, k: float) -> RGB:
    k = min(1.0, max(0.0, k))
    return (int(rgb[0] * k), int(rgb[1] * k), int(rgb[2] * k))


def lerp(a: RGB, b: RGB, t: float) -> RGB:
    t = min(1.0, max(0.0, t))
    return (int(a[0] + (b[0] - a[0]) * t), int(a[1] + (b[1] - a[1]) * t), int(a[2] + (b[2] - a[2]) * t))


def envelope(effect: str, phase: float) -> float:
    """Intensity 0..1 of the effect color at this point of its cycle."""
    frac = phase - math.floor(phase)
    if effect in ("flash", "brightness_blink"):
        return 1.0 if frac < 0.5 else 0.0
    return 0.5 - 0.5 * math.cos(2 * math.pi * frac)  # pulse: smooth in and out


def wave_zones(n: int, phase: float, color: RGB, base) -> list[RGB]:
    frac = phase - math.floor(phase)
    pos = frac * (n + 2) - 1  # sweep from before the first zone to past the last
    bases = zones_of(base, n)
    return [lerp(bases[i], color, max(0.0, 1.0 - abs(pos - i) / 1.2)) for i in range(n)]


def zones_of(base, n: int) -> list[RGB]:
    """A base color (single RGB, per-zone list, or None) as exactly n zones."""
    if isinstance(base, list):
        return (base + [base[-1] if base else (0, 0, 0)] * n)[:n]
    return [base or (0, 0, 0)] * n


def agent_sessions(sessions: list[dict], agent: str) -> list[dict]:
    """The sessions one action looks at. Filtering by agent re-ranks them, so a
    keyboard showing only Claude has no holes where Codex sessions sit."""
    if not agent:
        return list(sessions)
    mine = sorted((s for s in sessions if s.get("agent") == agent), key=lambda s: int(s.get("slot", 0)))
    return [{**s, "slot": i} for i, s in enumerate(mine)]


_URGENCY = {"input": 0, "running": 1}


def session_zones(n: int, sessions: list[dict], action: Action) -> list[RGB]:
    """Zone i shows the status color of the session in slot i + offset; empty slots are dark.

    With more sessions than zones, a session that needs you (or is working)
    beyond the last zone borrows a free zone, else the zone of the last idle
    session: the whole point of the keyboard is to see the tab that is busy."""
    if not sessions:
        # Nothing to lay out: show the idle colour, the same as the folded layout
        # does. A device dedicated to one agent going black the moment that agent
        # has no tabs open reads as broken, not as idle.
        return [scale(action.palette.get("done", (0, 0, 0)), action.brightness)] * n
    shown: dict[int, dict] = {}
    hidden = []
    for s in sessions:
        i = int(s.get("slot", -1)) - action.offset
        if 0 <= i < n:
            shown[i] = s
        elif i >= n and s.get("status") in _URGENCY:
            hidden.append(s)  # slots before the offset belong to another device
    for s in sorted(hidden, key=lambda s: (_URGENCY[s["status"]], int(s.get("slot", 0)))):
        spare = [i for i in range(n) if i not in shown or shown[i].get("status") not in _URGENCY]
        if not spare:
            break
        shown[min(spare, key=lambda i: (i in shown, -i))] = s  # a free zone first, then the last idle one
    zones = [(0, 0, 0)] * n
    for i, s in shown.items():
        zones[i] = scale(action.palette.get(s.get("status"), (0, 0, 0)), action.brightness)
    return zones


class EffectPlayer:
    # Set by the engine: called when a device stops accepting writes.
    on_device_error: Callable[[Device, Exception], None] | None = None

    def __init__(self, fps: int = 30, reduce_flashing: bool = False):
        self.fps = fps
        self.reduce_flashing = reduce_flashing
        self.quiet_hours: dict = {}          # settings["quiet_hours"], kept live by the engine
        self.gain: dict[str, float] = {}     # device id -> 0..1 dimmer from the Devices page
        self._devices: dict[str, Device] = {}
        self._base: dict[str, RGB | list[RGB]] = {}
        self._base_level: dict[str, float] = {}
        self._active: dict[str, Transient] = {}
        self._last_write: dict[str, tuple] = {}
        self._last_write_at: dict[str, float] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self.paused = threading.Event()
        self._thread: threading.Thread | None = None

    # --- lifecycle -----------------------------------------------------------
    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="lumen-effects", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def set_devices(self, devices: list[Device]) -> None:
        with self._lock:
            self._devices = {d.id: d for d in devices}
            for gone in set(self._active) - set(self._devices):
                self._active.pop(gone, None)
            self._last_write.clear()  # new handles: repaint everything

    def quiet_mode(self) -> str:
        """"" outside quiet hours, else "no_flash" or "dark"."""
        return self.quiet_hours.get("mode", "no_flash") if within(self.quiet_hours) else ""

    def current_color(self, device_id: str) -> RGB | None:
        """What the device shows right now (first zone for zoned writes)."""
        zones = self.current_zones(device_id)
        return zones[0] if zones else None

    def current_zones(self, device_id: str) -> list[RGB] | None:
        """Per-zone colors the device shows right now (one entry for single-color writes)."""
        last = self._last_write.get(device_id)
        if last and isinstance(last[0], tuple):
            return [c for c in last if isinstance(c, tuple)]
        base = self._base.get(device_id)
        if base is None:
            return None
        return list(base) if isinstance(base, list) else [base]

    # --- actions -------------------------------------------------------------
    def targets(self, action: Action) -> list[Device]:
        need = str(EFFECTS.get(action.effect, {}).get("needs") or "")
        with self._lock:
            devices = list(self._devices.values())
        if action.device not in ("*", "all", ""):
            devices = [d for d in devices if d.id == action.device]
        elif EFFECTS.get(action.effect, {}).get("persistent"):
            devices = [d for d in devices if d.ambient]  # wildcard base colors skip the screen glow
        # a device qualifies if it has the capability, or can degrade a color effect to brightness
        return [d for d in devices if d.supports(need) or (need in (COLOR, ZONES) and d.supports(BRIGHTNESS))]

    def run(self, action: Action, event: Event | None = None) -> list[str]:
        """Apply one action. Returns the ids of the devices it touched."""
        touched = []
        for device in self.targets(action):
            try:
                self._apply(device, action, event)
                touched.append(device.id)
            except Exception as e:
                self._fail(device, e)
        return touched

    def _apply(self, device: Device, action: Action, event: Event | None) -> None:
        effect = action.effect
        if self.quiet_mode() and effect not in ("notify", "sound") and not EFFECTS.get(effect, {}).get("persistent"):
            return  # quiet hours: base colors still track reality, nothing flashes
        if effect == "sessions":
            sessions = agent_sessions((event.data.get("sessions") if event else None) or [], action.agent)
            if action.per_zone and device.supports(ZONES) and device.zone_count > 1:
                zones = session_zones(device.zone_count, sessions, action)
                with self._lock:
                    self._base[device.id] = zones
                    self._active.pop(device.id, None)
                    self._write_zones(device, zones, force=True)
                return
            # whole device shows the folded status of the (filtered) sessions; none open = idle = done
            import dataclasses
            folded = next((s for s in ("input", "running") if any(x.get("status") == s for x in sessions)), "done")
            action = dataclasses.replace(action, effect="set", color=action.palette.get(folded, (0, 0, 0)))
            effect = action.effect
        if effect == "notify":
            title, body = _notification_text(action, event)
            threading.Thread(target=device.notify, args=(title, body), daemon=True).start()
            return
        if effect == "sound":
            threading.Thread(target=_play_sound, args=(device, action.sound, self.gain.get(device.id, 1.0)),
                             daemon=True).start()
            return
        if effect == "wave" and not device.supports(ZONES):
            effect = "pulse"
        if not device.supports(COLOR):
            effect = _DEGRADE.get(effect, effect)
        color = scale(action.color, action.brightness)
        duration, count = action.duration, action.count
        if self.reduce_flashing and effect in ("flash", "brightness_blink"):
            effect = "pulse" if effect == "flash" else "brightness_pulse"
        if self.reduce_flashing:
            duration = max(duration, count / 2.0)  # never faster than 2 Hz
        with self._lock:
            if effect == "set":
                self._base[device.id] = color
                self._active.pop(device.id, None)
                self._write(device, color, force=True)
            elif effect == "off":
                self._base[device.id] = (0, 0, 0)
                self._active.pop(device.id, None)
                self._write(device, (0, 0, 0), force=True)
            elif effect == "brightness_set":
                self._base_level[device.id] = action.brightness
                self._active.pop(device.id, None)
                self._write_level(device, action.brightness, force=True)
            elif effect == "brightness_off":
                self._base_level[device.id] = 0.0
                self._active.pop(device.id, None)
                self._write_level(device, 0.0, force=True)
            else:
                saved = None
                if device.id not in self._base and device.id not in self._base_level:
                    try:
                        saved = device.remember()
                    except Exception:
                        saved = None
                self._active[device.id] = Transient(effect, color, time.monotonic(), duration, count,
                                                    action.brightness, saved)

    # --- loop ----------------------------------------------------------------
    def _loop(self) -> None:
        tick = 1.0 / self.fps
        quiet = self.quiet_mode()
        while not self._stop.is_set():
            if not self.paused.is_set():
                if (mode := self.quiet_mode()) != quiet:
                    quiet = mode
                    self.repaint()  # the window opened or closed: put the right colours back
                self._tick(time.monotonic())
            time.sleep(tick)

    def repaint(self) -> None:
        """Re-send every device's base state. Used when something outside the
        effects themselves changed what a write should produce (quiet hours
        starting, a dimmer moving)."""
        with self._lock:
            self._last_write.clear()
            for device in list(self._devices.values()):
                try:
                    if (base := self._base.get(device.id)) is not None:
                        self._write(device, base, force=True)
                    elif (level := self._base_level.get(device.id)) is not None:
                        self._write_level(device, level, force=True)
                except Exception as e:
                    self._fail(device, e)

    def _tick(self, now: float) -> None:
        with self._lock:
            devices = list(self._devices.values())
            for device in devices:
                try:
                    self._tick_device(device, now)
                except Exception as e:
                    self._fail(device, e)

    def _tick_device(self, device: Device, now: float) -> None:
        active = self._active.get(device.id)
        base = self._base.get(device.id)
        level = self._base_level.get(device.id)
        if active is None:
            if device.refresh_every_s and now - self._last_write_at.get(device.id, 0) >= device.refresh_every_s:
                if base is not None:
                    self._write(device, base, force=True)
                elif level is not None:
                    self._write_level(device, level, force=True)
            return
        phase = active.phase(now)
        if phase is None:  # over: put the base (or the remembered state) back, else go dark
            self._active.pop(device.id, None)
            self._last_write.pop(device.id, None)  # direct-mode hardware may have been repainted
            if base is not None:
                self._write(device, base, force=True)
            elif level is not None:
                self._write_level(device, level, force=True)
            elif active.saved is not None:
                device.restore(active.saved)
            elif device.supports(COLOR) or device.supports(ZONES):
                self._write(device, (0, 0, 0), force=True)  # through the tracker, so the next flash isn't skipped
            elif device.supports(BRIGHTNESS):
                self._write_level(device, 0.0, force=True)
            else:
                device.off()
            return
        if active.effect in ("brightness_blink", "brightness_pulse"):
            self._write_level(device, envelope(active.effect, phase) * active.brightness)
        elif active.effect == "wave":
            zones = wave_zones(device.zone_count, phase, active.color, base)
            self._write_zones(device, zones)
        elif isinstance(base, list):  # flash/pulse on top of per-zone colors: blend every zone
            k = envelope(active.effect, phase)
            self._write_zones(device, [lerp(z, active.color, k) for z in zones_of(base, device.zone_count)])
        else:
            self._write(device, lerp(base or (0, 0, 0), active.color, envelope(active.effect, phase)))

    # --- writes (only when something changed, and not faster than the device likes)
    def _paint(self, device: Device, color: RGB) -> RGB:
        """Everything a device is told to show passes through here: the user's
        per-device dimmer, and darkness during "dark" quiet hours."""
        if self.quiet_mode() == "dark":
            return (0, 0, 0)
        gain = self.gain.get(device.id, 1.0)
        return color if gain >= 1.0 else scale(color, gain)

    def _level(self, device: Device, level: float) -> float:
        if self.quiet_mode() == "dark":
            return 0.0
        return level * self.gain.get(device.id, 1.0)

    def _throttled(self, device: Device, now: float) -> bool:
        max_fps = getattr(device, "max_fps", None)
        return bool(max_fps) and now - self._last_write_at.get(device.id, 0) < 1.0 / max_fps

    def _write(self, device: Device, color, force: bool = False) -> None:
        if isinstance(color, list):  # a per-zone base (sessions effect)
            return self._write_zones(device, zones_of(color, device.zone_count), force)
        now = time.monotonic()
        color = self._paint(device, color)
        if not force and (self._last_write.get(device.id) == (color,) or self._throttled(device, now)):
            return
        device.set_color(color)
        self._last_write[device.id] = (color,)
        self._last_write_at[device.id] = now

    def _write_zones(self, device: Device, zones: list[RGB], force: bool = False) -> None:
        now = time.monotonic()
        zones = [self._paint(device, z) for z in zones]
        key = tuple(zones)
        if not force and (self._last_write.get(device.id) == key or self._throttled(device, now)):
            return
        device.set_zones(zones)
        self._last_write[device.id] = key
        self._last_write_at[device.id] = now

    def _write_level(self, device: Device, level: float, force: bool = False) -> None:
        now = time.monotonic()
        level = round(min(1.0, max(0.0, self._level(device, level))), 2)
        if not force and (self._last_write.get(device.id) == ("level", level) or self._throttled(device, now)):
            return
        device.set_brightness(level)
        self._last_write[device.id] = ("level", level)
        self._last_write_at[device.id] = now

    def _fail(self, device: Device, exc: Exception) -> None:
        self._active.pop(device.id, None)
        if device.connected:
            device.connected = False
            print(f"effects: {device.name}: {type(exc).__name__}: {exc}", flush=True)
            if self.on_device_error:
                self.on_device_error(device, exc)


def _play_sound(device: Device, name: str, volume: float) -> None:
    """The device's dimmer is its volume. Adapters written before volume existed
    take the name alone, so fall back rather than dropping their sound."""
    try:
        device.play_sound(name, volume)
    except TypeError:
        device.play_sound(name)


def _notification_text(action: Action, event: Event | None) -> tuple[str, str]:
    data = dict(event.data) if event else {}
    label = CATALOG.get(event.type, {}).get("label", event.type) if event else "Lumen"
    data.setdefault("event", event.type if event else "test")
    body = action.message or label
    try:
        body = body.format_map(_Safe(data))
    except (ValueError, IndexError):
        pass
    agent = data.get("agent")
    title = f"Lumen · {str(agent).title()}" if agent else "Lumen"
    return title, body


class _Safe(dict):
    def __missing__(self, key):
        return "{" + key + "}"
