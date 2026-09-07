"""The engine wires everything together:

    integrations ──emit──▶ EventBus ──▶ rules ──▶ EffectPlayer ──▶ devices

It owns device discovery (initial scan plus a periodic rescan), the config,
and a small log of which rules fired for which event, which is what the
dashboard shows. The HTTP API in lumen.server is a thin layer over this class.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable

from lumen.core import devices as devmod
from lumen.core.config import Config
from lumen.core.effects import EffectPlayer
from lumen.core.events import Event, EventBus
from lumen.core.integrations import Integration, integration_classes
from lumen.core.rules import EFFECTS, Action


def _release(device: devmod.Device) -> None:
    """Fully let go of a device (shutdown/pause). Singletons like the screen
    glow keep their helper process across rescans but end it here."""
    try:
        getattr(device, "shutdown", device.close)()
    except Exception:
        pass


class Engine:
    # Set by the app: shut the process down cleanly (the self-updater uses it).
    on_exit_request: Callable[[], None] | None = None

    def __init__(self, config: Config | None = None, integrations: list[type[Integration]] | None = None,
                 discover=devmod.discover_all):
        self.config = config or Config()
        self.bus = EventBus()
        self.player = EffectPlayer(reduce_flashing=self.config.settings["reduce_flashing"])
        self.player.on_device_error = self._device_failed
        self.devices: list[devmod.Device] = []
        self.integrations: list[Integration] = []
        self._integration_classes = integrations if integrations is not None else integration_classes()
        self._discover = discover
        self.activity: deque[dict] = deque(maxlen=200)   # events + which rules fired
        self._persistent: dict[str, tuple[Action, Event]] = {}  # last `set`/`off` per rule, replayed on new devices
        self._last_event: dict[str, Event] = {}  # newest event of each type, replayed when a rule changes
        self.messages: deque[dict] = deque(maxlen=50)    # engine notes for the dashboard
        self.started_at = time.time()
        self.revision = 0                       # bumped whenever state() would look different
        self._last_error_scan = 0.0
        self._changed = threading.Condition()
        self._stop = threading.Event()
        self._scan_lock = threading.Lock()
        self.scanning = False
        self.bus.subscribe(self._on_event)
        self._sync_player()

    # --- live updates ---------------------------------------------------------
    def bump(self) -> int:
        """Say that the dashboard's view is out of date. /api/stream waits on
        this instead of the client polling a large state object every 2 s."""
        with self._changed:
            self.revision += 1
            self._changed.notify_all()
            return self.revision

    def wait_for_change(self, seen: int, timeout: float) -> int:
        """Block until the revision moves past `seen` (or the timeout runs out)."""
        with self._changed:
            if self.revision <= seen:
                self._changed.wait(timeout)
            return self.revision

    def _sync_player(self) -> None:
        """Push the settings the effect player needs to read on every write."""
        self.player.reduce_flashing = self.config.settings["reduce_flashing"]
        self.player.quiet_hours = dict(self.config.settings["quiet_hours"])
        self.player.gain = {device_id: float(opts["brightness"])
                            for device_id, opts in self.config.data["devices"].items()
                            if isinstance(opts.get("brightness"), (int, float))}

    def apply_settings(self) -> None:
        """Settings changed underneath us: re-read the ones that affect writes
        and repaint, so a dimmer or a quiet-hours edit shows up immediately."""
        self._sync_player()
        self.player.repaint()
        self.bump()

        def rescan_then_repaint() -> None:
            # A built-in the user switched on or off (the notch tab) appears or
            # goes here, and settings that live *on* a device — where the status
            # tab sits, which meters it shows, whose sessions it follows — are
            # read by its discover(). So the repaint above can only show the old
            # values: it ran before this. Repainting again once the scan has
            # settled is what makes the change visible now, rather than whenever
            # the next event happens to write to the device.
            self.scan()
            self.player.repaint()

        threading.Thread(target=rescan_then_repaint, name="lumen-settings-scan", daemon=True).start()

    def request_exit(self) -> None:
        """Ask the host process to shut down cleanly (after the response is sent)."""
        if self.on_exit_request:
            threading.Timer(0.5, self.on_exit_request).start()

    # --- lifecycle -----------------------------------------------------------
    def start(self) -> None:
        self.player.start()
        self.scan()  # devices first, so the integrations' initial status events land on them
        for cls in self._integration_classes:
            try:
                integ = cls(self.bus.emit, self.config.integration_options(cls.id))
                integ.settings = self.config.settings  # live view, e.g. the webhook needs the port
                integ.start()
                self.integrations.append(integ)
            except Exception as e:
                self._log(f"integration {getattr(cls, 'id', cls)} failed to start: {type(e).__name__}: {e}")
        threading.Thread(target=self._rescan_loop, name="lumen-rescan", daemon=True).start()

    @property
    def stopping(self) -> bool:
        return self._stop.is_set()

    def stop(self) -> None:
        self._stop.set()
        self.bump()  # wake every open /api/stream so its thread can finish
        self.player.stop()
        for integ in self.integrations:
            try:
                integ.stop()
            except Exception:
                pass
        for d in self.devices:
            _release(d)

    @property
    def paused(self) -> bool:
        return self.player.paused.is_set()

    def set_paused(self, value: bool) -> None:
        if value:
            self.player.paused.set()
            if self.config.settings.get("keep_lit"):
                # Pause means "stop reacting". With this on it does not also mean
                # "go dark": the device keeps the colour it is showing. Holding the
                # handle open is the only way to do that — a keyboard in direct
                # mode falls back to its own stored profile the moment the host
                # stops talking to it, so letting go *is* what turns it off.
                return
            for d in self.devices:  # give the hardware back to its firmware / vendor software
                _release(d)
            self.devices = []
            self.player.set_devices([])
        else:
            self.player.paused.clear()
            self.scan()

    # --- devices -------------------------------------------------------------
    def scan(self) -> list[devmod.Device]:
        if not self._scan_lock.acquire(blocking=False):
            return self.devices
        self.scanning = True
        try:
            old = {d.id: d for d in self.devices}
            found = self._discover(self._device_settings())
            for d in found:
                opts = self.config.device_options(d.id)
                if opts.get("name"):
                    d.name = opts["name"]
                d.details["enabled"] = opts.get("enabled", True)
            for stale in set(old) - {d.id for d in found}:
                try:
                    old[stale].close()
                except Exception:
                    pass
            for d in found:
                if d.id in old and old[d.id] is not d:
                    try:
                        old[d.id].close()
                    except Exception:
                        pass
            self.devices = found
            self.player.set_devices([d for d in found if d.details.get("enabled", True)])
            for d in found:  # a device that just appeared inherits the current status colors
                if d.id not in old:
                    self._replay_persistent(d)
            self.bump()
            return found
        finally:
            self.scanning = False
            self._scan_lock.release()

    def _device_failed(self, device: devmod.Device, exc: Exception) -> None:
        device.details["last_error"] = f"{type(exc).__name__}: {exc}"[:200]
        self._log(f"{device.name} disconnected ({type(exc).__name__})")
        self.bump()
        # A handle dies when the machine suspends or a cable moves. Waiting out
        # the rescan interval (a minute by default) leaves the device dark for
        # no reason; rescan now, debounced so a burst of errors is one scan.
        if time.time() - self._last_error_scan > 5:
            self._last_error_scan = time.time()
            threading.Thread(target=self.scan, name="lumen-recover", daemon=True).start()

    def _replay_persistent(self, device: devmod.Device) -> None:
        import dataclasses
        for action, event in list(self._persistent.values()):
            wildcard = action.device in ("*", "all", "")
            if action.device == device.id or (wildcard and device.ambient):
                self.player.run(dataclasses.replace(action, device=device.id), event)

    def _device_settings(self) -> dict:
        return {**self.config.settings, "devices": dict(self.config.data["devices"]),
                "integrations": dict(self.config.data["integrations"])}

    def _rescan_loop(self) -> None:
        while not self._stop.wait(5):
            interval = self.config.settings.get("rescan_interval_s") or 0
            if interval and not self.paused and time.time() - getattr(self, "_last_scan", 0) >= interval:
                self._last_scan = time.time()
                self.scan()

    def set_device_options(self, device_id: str, patch: dict) -> dict:
        if "brightness" in patch:
            try:
                patch = {**patch, "brightness": min(1.0, max(0.05, float(patch["brightness"])))}
            except (TypeError, ValueError):
                raise ValueError("brightness: expected a number between 0.05 and 1") from None
        opts = self.config.set_device_options(device_id, patch)
        for d in self.devices:
            if d.id == device_id:
                if "name" in patch:
                    d.name = patch["name"] or d.name
                d.details["enabled"] = opts.get("enabled", True)
        self.player.set_devices([d for d in self.devices if d.details.get("enabled", True)])
        self._sync_player()
        if "brightness" in patch:
            self.player.repaint()
        self.bump()
        return opts

    def forget_device(self, device_id: str) -> bool:
        """Drop the saved name/dimmer/enabled flag for a device that is gone.
        A connected device would just be rediscovered with its defaults."""
        if any(d.id == device_id for d in self.devices):
            raise ValueError("that device is connected; unplug it first")
        return self.config.forget_device(device_id)

    def test_device(self, device_id: str, action: dict | None = None) -> list[str]:
        act = Action.from_dict({"effect": "flash", "color": (0, 255, 0), **(action or {}), "device": device_id})
        if act.effect in ("notify", "sound"):
            act.message = act.message or "This is a test from Lumen."
        return self.player.run(act, Event("test", "dashboard", {"device": device_id}))

    # --- events & rules ------------------------------------------------------
    def emit(self, type_: str, data: dict | None = None, source: str = "api") -> Event:
        ev = Event(type_, source, data or {})
        self.bus.emit(ev)
        return ev

    def _on_event(self, event: Event) -> None:
        self._last_event[event.type] = event
        fired = []
        for rule in self.config.rules:
            if rule.matches(event):
                fired.append(rule.name or rule.id)
                for action in rule.actions:
                    self.player.run(action, event)
                    if EFFECTS.get(action.effect, {}).get("persistent"):
                        self._persistent.pop(rule.id, None)
                        self._persistent[rule.id] = (action, event)  # newest last
        self.activity.append({**event.to_dict(), "rules": fired})
        self.bump()
        if self.config.settings.get("log_events"):
            print(f"{time.strftime('%H:%M:%S')} event {event.type} {event.data} -> {fired or 'no rule'}", flush=True)

    def reapply(self) -> None:
        """Re-run the persistent actions of every rule against the last event of
        its kind. Without this an edited rule sits dark until the next event
        happens to arrive, which for an idle agent can be hours — the dashboard
        control looks broken when it worked perfectly."""
        for rule in self.config.rules:
            if not rule.enabled:
                continue
            for event in list(self._last_event.values()):
                if not rule.matches(event):
                    continue
                for action in rule.actions:
                    if EFFECTS.get(action.effect, {}).get("persistent"):
                        self.player.run(action, event)
                        self._persistent.pop(rule.id, None)
                        self._persistent[rule.id] = (action, event)
        self.bump()

    def clear_activity(self) -> None:
        self.activity.clear()
        self.messages.clear()
        self.bump()

    def integration(self, integration_id: str) -> Integration | None:
        return next((i for i in self.integrations if i.id == integration_id), None)

    def set_integration_options(self, integration_id: str, patch: dict) -> dict:
        opts = self.config.set_integration_options(integration_id, patch)
        integ = self.integration(integration_id)
        if integ:
            integ.set_options(opts)
        self.bump()
        return opts

    # --- dashboard snapshot ----------------------------------------------------
    def _log(self, text: str) -> None:
        print(f"{time.strftime('%H:%M:%S')} engine: {text}", flush=True)
        self.messages.append({"ts": time.time(), "text": text})

    def _autostart_now(self) -> bool:
        """Whether the OS will actually start Lumen at login, not what the config
        remembers wanting. The tray toggles the login entry directly and the entry
        can be removed by anything else on the machine, so a stored preference is
        a wish; the registry key or the plist is the fact. Reported rather than
        stored so the dashboard and the tray menu cannot disagree."""
        try:
            from lumen.app import autostart
            return autostart.enabled()
        except Exception:      # no desktop layer, or an OS that refuses the read
            return bool(self.config.settings.get("autostart"))

    def state(self) -> dict:
        from lumen import __version__
        from lumen.core.events import CATALOG
        from lumen.core.rules import EFFECTS
        return {
            "version": __version__,
            "revision": self.revision,
            "uptime_s": int(time.time() - self.started_at),
            "paused": self.paused,
            "scanning": self.scanning,
            "onboarded": self.config.data["onboarded"],
            "settings": {**self.config.settings, "autostart": self._autostart_now()},
            "devices": [{**d.to_dict(), "color": self.player.current_color(d.id),
                         "brightness": self.config.device_options(d.id).get("brightness", 1.0),
                         "zone_colors": self.player.current_zones(d.id)} for d in self.devices],
            "forgotten_devices": self.config.saved_device_ids(exclude={d.id for d in self.devices}),
            "integrations": [i.to_dict() for i in self.integrations],
            "sessions": sorted((s for i in self.integrations for s in getattr(i, "sessions", [])),
                               key=lambda s: s.get("slot", 0)),
            "rules": [r.to_dict() for r in self.config.rules],
            "presets": self.config.presets,
            "quiet_now": bool(self.player.quiet_mode()),
            "activity": list(self.activity)[-40:][::-1],
            "messages": list(self.messages)[-10:][::-1],
            "catalog": CATALOG,
            "effects": EFFECTS,
        }
