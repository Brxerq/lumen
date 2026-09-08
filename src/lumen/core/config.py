"""config.json: rules, settings and per-device/integration options, one file.

    {
      "version": 1,
      "onboarded": false,
      "settings": { ...DEFAULT_SETTINGS },
      "rules": [ {Rule}, ... ],
      "devices": { "<device id>": {"enabled": true, "name": "..."} },
      "integrations": { "<integration id>": {...} }
    }

Reads tolerate a missing or broken file (you get defaults); writes are atomic.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import uuid
from collections.abc import Collection
from pathlib import Path

from lumen import paths
from lumen.core.rules import Rule, default_rules

DEFAULT_SETTINGS = {
    "port": 6733,                 # dashboard + API, loopback only
    "autostart": False,           # mirrored to the OS at save time by app/autostart.py
    "start_minimized": True,      # don't open the dashboard when the daemon starts
    "rescan_interval_s": 60,      # background device rescan; 0 = manual only
    "reduce_flashing": False,     # accessibility: no strobing, flashes become pulses <= 2 Hz
    "keep_lit": False,            # pausing stops reacting, but leaves the light where it is
    "webhook_token": "",          # if set, POST /api/events must send it as a bearer token
    "launch_openrgb": True,       # start the OpenRGB server if the app is installed but idle
    "notch": True,                # the status tab at the top of the screen (devices/notch.py)
    "notch_position": "top",      # top | top-left | top-right | bottom | left | right
    "notch_idle_hide_min": 0,     # hide the tab once no agent has done anything for N minutes; 0 = never
    "notch_completed_hide_min": 30,  # remove completed tabs from the status tab after N minutes; 0 = never
    "notch_offset": -1,           # where along the edge, 0..100 % of the screen width; -1 = the preset above
    "notch_size": "regular",      # thin | regular | thick
    "notch_opacity": 96,          # 30..100 %
    "notch_hide_fullscreen": True,  # stay out of the way of full-screen games and films
    # what the tab shows; untick down to "just my Claude limits and the live tabs"
    "notch_agents": "all",          # all | claude | codex
    "notch_show_sessions": True,
    "notch_show_context": True,
    "notch_show_cost": True,
    "notch_show_activity": True,
    "notch_show_claude_usage": True,
    "notch_show_codex_usage": True,
    "notch_show_accent": True,      # the agent's colour as a cap on each bar
    "notch_show_usage_follows_tabs": False,  # hide an agent's limit meter while it has no tab open
    "log_events": True,
    # No flashing (or no light at all) between these hours. Local time, and a
    # window that wraps midnight is the normal case.
    "quiet_hours": {"enabled": False, "from": "23:00", "to": "07:00", "mode": "no_flash"},
}
QUIET_MODES = ("no_flash", "dark")
_HHMM = re.compile(r"([01]\d|2[0-3]):[0-5]\d")

# Settings a user can put out of range. Values outside these bounds are refused
# rather than clamped: a silently moved port is worse than an error message.
SETTING_RANGES = {"port": (1024, 65535), "rescan_interval_s": (0, 86400),
                  "notch_offset": (-1, 100), "notch_opacity": (30, 100), "notch_idle_hide_min": (0, 1440),
                  "notch_completed_hide_min": (0, 1440)}


def _coerce(key: str, value):
    default = DEFAULT_SETTINGS[key]
    if key == "quiet_hours":
        return _quiet_hours(value)
    if isinstance(default, bool):
        return bool(value)
    try:
        value = type(default)(value)
    except (TypeError, ValueError):
        raise ValueError(f"{key}: expected {type(default).__name__}") from None
    if key == "notch_position" and value not in ("top", "top-left", "top-right", "bottom", "left", "right"):
        raise ValueError("notch_position: top, top-left, top-right, bottom, left or right")
    if key == "notch_agents" and value not in ("all", "claude", "codex"):
        raise ValueError("notch_agents: all, claude or codex")
    if key == "notch_size" and value not in ("thin", "regular", "thick"):
        raise ValueError("notch_size: thin, regular or thick")
    if key in SETTING_RANGES:
        low, high = SETTING_RANGES[key]
        assert isinstance(value, int)
        if not low <= value <= high:
            raise ValueError(f"{key}: must be between {low} and {high}")
    return value


def _settings(saved: dict) -> dict:
    """Defaults, overlaid with the saved values we still recognise. A value the
    current version cannot use (an old format, a hand-edited typo) falls back to
    the default rather than stopping the daemon from starting."""
    out = dict(DEFAULT_SETTINGS)
    for key, value in saved.items():
        if key not in DEFAULT_SETTINGS:
            continue
        try:
            out[key] = _coerce(key, value)
        except ValueError:
            pass
    return out


def _quiet_hours(value) -> dict:
    if not isinstance(value, dict):
        raise ValueError("quiet_hours: expected an object")
    out = dict(DEFAULT_SETTINGS["quiet_hours"])
    out["enabled"] = bool(value.get("enabled", out["enabled"]))
    for edge in ("from", "to"):
        text = str(value.get(edge, out[edge]))
        if not _HHMM.fullmatch(text):
            raise ValueError(f"quiet_hours.{edge}: expected HH:MM")
        out[edge] = text
    mode = str(value.get("mode", out["mode"]))
    if mode not in QUIET_MODES:
        raise ValueError(f"quiet_hours.mode: expected one of {', '.join(QUIET_MODES)}")
    out["mode"] = mode
    return out


class Config:
    def __init__(self, path: Path | None = None):
        self.path = path or paths.config_file()
        self._lock = threading.RLock()
        self.data = self._load()

    # --- persistence ---------------------------------------------------------
    def _load(self) -> dict:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
        data = {
            "version": 1,
            "onboarded": bool(raw.get("onboarded", False)),
            "settings": _settings(dict(raw.get("settings", {}))),
            "rules": [r.to_dict() for r in (
                [Rule.from_dict(x) for x in raw["rules"]] if isinstance(raw.get("rules"), list) else default_rules())],
            "devices": dict(raw.get("devices", {})),
            "integrations": dict(raw.get("integrations", {})),
            "presets": [p for p in raw.get("presets", []) if isinstance(p, dict) and p.get("id")],
        }
        return data

    def save(self) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".config-", suffix=".json")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2)
            os.replace(tmp, self.path)

    # --- accessors -------------------------------------------------------------
    @property
    def settings(self) -> dict:
        return self.data["settings"]

    def update_settings(self, patch: dict) -> dict:
        """Apply a partial settings object. Raises ValueError on a value the
        daemon could not start with (the API turns that into a 400)."""
        with self._lock:
            for k, v in patch.items():
                if k not in DEFAULT_SETTINGS:
                    continue
                self.settings[k] = _coerce(k, v)
            self.save()
            return dict(self.settings)

    @property
    def rules(self) -> list[Rule]:
        return [Rule.from_dict(r) for r in self.data["rules"]]

    def set_rules(self, rules: list[Rule]) -> None:
        with self._lock:
            self.data["rules"] = [r.to_dict() for r in rules]
            self.save()

    def upsert_rule(self, rule: Rule) -> Rule:
        with self._lock:
            rules = [r for r in self.rules if r.id != rule.id]
            existing = next((i for i, r in enumerate(self.data["rules"]) if r["id"] == rule.id), None)
            if existing is None:
                rules.append(rule)
            else:
                rules.insert(existing, rule)
            self.set_rules(rules)
            return rule

    def delete_rule(self, rule_id: str) -> bool:
        with self._lock:
            before = len(self.data["rules"])
            self.set_rules([r for r in self.rules if r.id != rule_id])
            return len(self.data["rules"]) < before

    @property
    def presets(self) -> list[dict]:
        """Named bundles of actions ("Success", "Attention") a rule can copy."""
        return [dict(p) for p in self.data["presets"]]

    def upsert_preset(self, preset: dict) -> dict:
        name = str(preset.get("name") or "").strip()
        actions = preset.get("actions")
        if not name or not isinstance(actions, list) or not actions:
            raise ValueError("a preset needs a name and at least one action")
        saved = {"id": str(preset.get("id") or uuid.uuid4().hex[:8]), "name": name[:60], "actions": actions}
        with self._lock:
            rest = [p for p in self.data["presets"] if p["id"] != saved["id"]]
            index = next((i for i, p in enumerate(self.data["presets"]) if p["id"] == saved["id"]), len(rest))
            rest.insert(index, saved)
            self.data["presets"] = rest
            self.save()
            return dict(saved)

    def delete_preset(self, preset_id: str) -> bool:
        with self._lock:
            before = len(self.data["presets"])
            self.data["presets"] = [p for p in self.data["presets"] if p["id"] != preset_id]
            self.save()
            return len(self.data["presets"]) < before

    def saved_device_ids(self, exclude: Collection[str] = ()) -> list[str]:
        """Devices we hold settings for that are not connected right now."""
        return sorted(set(self.data["devices"]) - set(exclude))

    def forget_device(self, device_id: str) -> bool:
        with self._lock:
            existed = self.data["devices"].pop(device_id, None) is not None
            if existed:
                self.save()
            return existed

    def device_options(self, device_id: str) -> dict:
        return dict(self.data["devices"].get(device_id, {}))

    def set_device_options(self, device_id: str, patch: dict) -> dict:
        with self._lock:
            opts = {**self.data["devices"].get(device_id, {}), **patch}
            self.data["devices"][device_id] = opts
            self.save()
            return opts

    def integration_options(self, integration_id: str) -> dict:
        return dict(self.data["integrations"].get(integration_id, {}))

    def set_integration_options(self, integration_id: str, patch: dict) -> dict:
        with self._lock:
            opts = {**self.data["integrations"].get(integration_id, {}), **patch}
            self.data["integrations"][integration_id] = opts
            self.save()
            return opts

    def set_onboarded(self, value: bool = True) -> None:
        with self._lock:
            self.data["onboarded"] = bool(value)
            self.save()
