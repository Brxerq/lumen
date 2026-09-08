"""Automation rules: WHEN an event matches THEN run actions on devices.

    Rule(when="agent.finished", match={"agent": "claude"},
         actions=[Action(device="*", effect="flash", color=(0,255,0), count=2)])

`when` is an event type, with fnmatch wildcards allowed ("build.*").
`match` is a subset of the event's data that must be equal (empty = any).
`device` is a device id, or "*" for every device that supports the effect.
"""

from __future__ import annotations

import fnmatch
import uuid
from dataclasses import asdict, dataclass, field

from lumen.core.events import Event

# Effects and the capability each one needs. The rule builder derives the
# per-device effect menu from this table plus Device.capabilities.
EFFECTS: dict[str, dict] = {
    "set":    {"label": "Set color",     "needs": "color",      "persistent": True,  "params": ["color", "brightness"]},
    "flash":  {"label": "Flash",         "needs": "color",      "persistent": False, "params": ["color", "count", "duration"]},
    "pulse":  {"label": "Pulse",         "needs": "color",      "persistent": False, "params": ["color", "count", "duration"]},
    "wave":   {"label": "Wave",          "needs": "zones",      "persistent": False, "params": ["color", "count", "duration"]},
    "off":    {"label": "Turn off",      "needs": "color",      "persistent": True,  "params": []},
    "brightness_pulse": {"label": "Pulse brightness", "needs": "brightness", "persistent": False, "params": ["count", "duration"]},
    "brightness_blink": {"label": "Blink backlight",  "needs": "brightness", "persistent": False, "params": ["count", "duration"]},
    "notify": {"label": "Notification",  "needs": "notify",     "persistent": False, "params": ["message"]},
    "sound":  {"label": "Play sound",    "needs": "sound",      "persistent": False, "params": ["sound"]},
    # The open agent sessions share the device (agents.sessions event): zone N shows the status
    # color of slot N + offset, and the tabs then widen to cover the zones none of them claimed —
    # one tab lights the whole device, two take half each. Devices without zones show the folded status.
    "sessions": {"label": "Agent status", "needs": "color", "persistent": True,
                 "params": ["agent", "per_zone", "palette", "offset", "brightness"]},
}

# A deeper green stays distinct from white backlighting and the dashboard's
# light-green accents, while still being bright enough for RGB hardware.
GREEN, AMBER, RED = (0, 143, 61), (255, 180, 0), (255, 0, 0)
DEFAULT_PALETTE = {"running": AMBER, "input": RED, "done": GREEN}


@dataclass
class Action:
    device: str = "*"
    effect: str = "flash"
    color: tuple = GREEN
    duration: float = 1.5       # seconds, whole effect
    count: int = 2              # flashes / pulses / wave passes
    brightness: float = 1.0     # 0..1, multiplies the color
    message: str = ""           # notify: body text ({event} placeholders allowed)
    sound: str = "default"      # sound: name known to the sound adapter
    palette: dict = field(default_factory=lambda: dict(DEFAULT_PALETTE))  # sessions: status -> color
    offset: int = 0             # sessions: first slot shown on this device (light bar = 4 for slots 5-6)
    agent: str = ""             # sessions: only this agent's sessions ("" = all), e.g. keyboard=claude, light bar=codex
    per_zone: bool = True       # sessions: the tabs share the zones; False = whole device shows the folded status

    @classmethod
    def from_dict(cls, d: dict) -> Action:
        a = cls()
        for k, v in d.items():
            if hasattr(a, k):
                setattr(a, k, v)
        a.color = _color(a.color)
        a.duration = max(0.1, float(a.duration))
        a.count = max(1, int(a.count))
        a.brightness = min(1.0, max(0.0, float(a.brightness)))
        a.offset = max(0, int(a.offset or 0))
        a.agent = str(a.agent or "").strip().lower()
        a.per_zone = bool(a.per_zone)
        a.palette = {k: _color((a.palette or {}).get(k, v)) for k, v in DEFAULT_PALETTE.items()}
        if a.effect not in EFFECTS:
            a.effect = "flash"
        return a

    def to_dict(self) -> dict:
        d = asdict(self)
        d["color"] = list(self.color)
        d["palette"] = {k: list(v) for k, v in self.palette.items()}
        return d


@dataclass
class Rule:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    name: str = ""
    enabled: bool = True
    when: str = "agent.finished"
    match: dict = field(default_factory=dict)
    actions: list = field(default_factory=list)

    def matches(self, event: Event) -> bool:
        if not self.enabled or not fnmatch.fnmatchcase(event.type, self.when):
            return False
        return all(_match_value(event.data.get(k), v) for k, v in self.match.items() if v not in ("", None, "*"))

    @classmethod
    def from_dict(cls, d: dict) -> Rule:
        return cls(
            id=str(d.get("id") or uuid.uuid4().hex[:8]),
            name=str(d.get("name", "")),
            enabled=bool(d.get("enabled", True)),
            when=str(d.get("when", "agent.finished")),
            match={k: v for k, v in dict(d.get("match", {})).items() if v not in ("", None)},
            actions=[Action.from_dict(a) for a in d.get("actions", [])],
        )

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "enabled": self.enabled, "when": self.when,
                "match": dict(self.match), "actions": [a.to_dict() for a in self.actions]}


_COMPARE = {">=": lambda a, b: a >= b, "<=": lambda a, b: a <= b, ">": lambda a, b: a > b, "<": lambda a, b: a < b}


def _match_value(actual, wanted) -> bool:
    """A filter value is an exact text match, or a numeric threshold when it starts
    with >=, <=, > or < ("five_hour_used" is ">= 90")."""
    text = str(wanted).strip()
    for op in (">=", "<=", ">", "<"):
        if text.startswith(op):
            try:
                return _COMPARE[op](float(actual), float(text[len(op):].strip()))
            except (TypeError, ValueError):
                return False
    return str(actual) == text


def _color(value) -> tuple:
    if isinstance(value, str) and value.startswith("#") and len(value) == 7:
        return tuple(int(value[i:i + 2], 16) for i in (1, 3, 5))
    try:
        r, g, b = (min(255, max(0, int(c))) for c in value)
        return (r, g, b)
    except (TypeError, ValueError):
        return GREEN


def default_rules() -> list[Rule]:
    """What a fresh install does: every light mirrors the overall agent status,
    multi-zone devices share their zones out between the open agent tabs, and a finished task
    gets a double green flash. The zone rule comes last so it wins on devices
    the status rules also paint."""
    return [
        Rule(name="Agent needs you", when="agents.status", match={"status": "input"},
             actions=[Action(device="*", effect="set", color=RED)]),
        Rule(name="Agent working", when="agents.status", match={"status": "running"},
             actions=[Action(device="*", effect="set", color=AMBER)]),
        Rule(name="All agents idle", when="agents.status", match={"status": "done"},
             actions=[Action(device="*", effect="set", color=GREEN)]),
        Rule(name="Task finished", when="agent.finished",
             actions=[Action(device="*", effect="flash", color=GREEN, count=2, duration=1.2)]),
        Rule(name="Build failed", when="build.failed",
             actions=[Action(device="*", effect="flash", color=RED, count=3, duration=1.5)]),
        Rule(name="A tab on every zone", when="agents.sessions",
             actions=[Action(device="*", effect="sessions")]),
    ]
