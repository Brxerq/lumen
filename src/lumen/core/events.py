"""Events: the one thing every integration produces and every rule consumes.

An event is a dotted type name plus a small data dict. Integrations emit them,
the engine matches them against rules, and the dashboard shows the last few.

Built-in types are listed in CATALOG so the rule builder can offer them; any
other dotted name is accepted too (webhooks and `lumen emit` send arbitrary ones).
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Event:
    type: str
    source: str = "cli"
    data: dict = field(default_factory=dict)
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {"type": self.type, "source": self.source, "data": dict(self.data), "ts": self.ts}


# type -> (label, description, filterable data keys with example values)
CATALOG: dict[str, dict] = {
    "agent.running":     {"label": "AI agent started working",   "fields": {"agent": ["claude", "codex"]}},
    "agent.needs_input": {"label": "AI agent is waiting for you", "fields": {"agent": ["claude", "codex"]}},
    "agent.finished":    {"label": "AI agent finished its task",  "fields": {"agent": ["claude", "codex"]}},
    "agents.status":     {"label": "Overall agent status changed", "fields": {"status": ["running", "input", "done"]}},
    "agents.sessions":   {"label": "Any agent session changed (per-tab)", "fields": {"status": ["running", "input", "done"]}},
    # usage limits: the fields are numbers, so the filter is a threshold (">= 90"), see rules._match_value
    "claude.usage":      {"label": "Claude usage limits changed", "fields": {"five_hour_used": [">= 50", ">= 70", ">= 90"],
                                                                             "seven_day_used": [">= 50", ">= 70", ">= 90"]}},
    "codex.usage":       {"label": "Codex usage limits changed", "fields": {"five_hour_used": [">= 50", ">= 70", ">= 90"],
                                                                            "seven_day_used": [">= 50", ">= 70", ">= 90"]}},
    "command.succeeded": {"label": "Command finished (exit 0)",   "fields": {"name": []}},
    "command.failed":    {"label": "Command failed (exit != 0)",  "fields": {"name": []}},
    "build.succeeded":   {"label": "Build succeeded",             "fields": {"name": []}},
    "build.failed":      {"label": "Build failed",                "fields": {"name": []}},
    "deploy.succeeded":  {"label": "Deployment succeeded",        "fields": {"name": []}},
    "deploy.failed":     {"label": "Deployment failed",           "fields": {"name": []}},
    "github.workflow.succeeded": {"label": "GitHub workflow succeeded", "fields": {"repo": [], "workflow": []}},
    "github.workflow.failed":    {"label": "GitHub workflow failed",    "fields": {"repo": [], "workflow": []}},
    "download.finished": {"label": "Download finished",           "fields": {"name": []}},
    "timer.finished":    {"label": "Timer finished",              "fields": {"name": []}},
    "notification":      {"label": "Generic notification",        "fields": {"title": []}},
    "test":              {"label": "Test event (from the dashboard)", "fields": {}},
}


class EventBus:
    """Synchronous fan-out with a bounded history. Subscribers must not block."""

    def __init__(self, history: int = 200):
        self._subs: list[Callable[[Event], None]] = []
        self._history: deque[Event] = deque(maxlen=history)
        self._lock = threading.Lock()

    def subscribe(self, fn: Callable[[Event], None]) -> None:
        with self._lock:
            self._subs.append(fn)

    def emit(self, event: Event) -> None:
        with self._lock:
            self._history.append(event)
            subs = list(self._subs)
        for fn in subs:
            try:
                fn(event)
            except Exception as e:  # one bad subscriber must not stop the others
                print(f"events: subscriber {getattr(fn, '__name__', fn)} failed: {type(e).__name__}: {e}", flush=True)

    def recent(self, n: int = 50) -> list[Event]:
        with self._lock:
            return list(self._history)[-n:]
