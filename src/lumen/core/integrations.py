"""Integration model and registry.

An integration is a source of events: an AI coding agent, a CI system, a
webhook, your shell. Each is a module in `lumen.integrations` (or a package
exposing the `lumen.integrations` entry point) that defines one subclass of
Integration and a module-level `INTEGRATION = TheClass`.

Lifecycle: the engine constructs it with an `emit` callback and its saved
options, calls start() once, and stop() on shutdown. status() feeds the
dashboard; connect()/disconnect() are optional one-click setup steps (for
example, installing agent hooks) and may return a message for the UI.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable

from lumen.core.events import Event

BUILTIN_INTEGRATIONS = [
    "lumen.integrations.claude_code",
    "lumen.integrations.codex",
    "lumen.integrations.webhook",
    "lumen.integrations.terminal",
    "lumen.integrations.github",
]


class Integration:
    settings: dict = {}           # live view of the global settings, set by the engine
    id = "base"
    name = "Integration"
    description = ""
    docs = ""                     # short markdown shown on the Integrations page
    events: tuple[str, ...] = ()  # event types this integration emits
    can_connect = False           # True if connect()/disconnect() do something
    option_fields: dict = {}      # {"repos": {"label": "...", "type": "text", "placeholder": "..."}}

    def __init__(self, emit: Callable[[Event], None], options: dict):
        self.emit = emit
        self.options = options

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def status(self) -> dict:
        """{"connected": bool, "detail": "human text"}"""
        return {"connected": True, "detail": ""}

    def connect(self) -> str:
        return "Nothing to set up."

    def disconnect(self) -> str:
        return "Nothing to remove."

    def set_options(self, options: dict) -> None:
        self.options = options

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "description": self.description, "docs": self.docs,
                "events": list(self.events), "can_connect": self.can_connect,
                "option_fields": self.option_fields, "options": dict(self.options), **self.status()}


def integration_classes() -> list[type[Integration]]:
    classes = []
    for name in BUILTIN_INTEGRATIONS:
        try:
            classes.append(importlib.import_module(name).INTEGRATION)
        except Exception as e:
            print(f"integrations: {name} failed to import: {type(e).__name__}: {e}", flush=True)
    try:
        from importlib.metadata import entry_points
        for ep in entry_points(group="lumen.integrations"):
            try:
                classes.append(ep.load())
            except Exception as e:
                print(f"integrations: plugin {ep.name} failed to load: {type(e).__name__}: {e}", flush=True)
    except Exception:
        pass
    return classes
