"""Remembered keyboard zones for agent sessions.

A live Claude Code or Codex tab gets a *slot* — zone 0, 1, 2… of a multi-zone
device. By default a new tab opens on zone 0 and pushes the rest along, which is
fine until you have four tabs and start caring which is which. Once you drag a
tab to a zone in the dashboard, that choice is remembered here, keyed twice:

    id:<session id>   this exact tab, for as long as it lives
    cwd:<project>     the next tab you open in that project lands there too

Kept in its own small file next to config.json, because it is the one piece of
state that changes without the user asking and would otherwise churn the config.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from pathlib import Path

from lumen import paths

FORGET_AFTER_S = 7 * 24 * 3600
_lock = threading.Lock()


def _file(home: Path | None = None) -> Path:
    return (home or paths.data_dir()) / "slots.json"


def load(home: Path | None = None) -> dict[str, dict]:
    try:
        data = json.loads(_file(home).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save(pins: dict[str, dict], home: Path | None = None, now: float | None = None) -> None:
    now = now or time.time()
    fresh = {k: v for k, v in pins.items() if now - v.get("ts", now) < FORGET_AFTER_S}
    path = _file(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".slots-", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(fresh, f, indent=2)
    os.replace(tmp, path)


def keys_for(session_id: str, cwd: str = "") -> list[str]:
    """Where a session's pin might be found, most specific first."""
    keys = [f"id:{session_id}"]
    if cwd:
        keys.append("cwd:" + normalise_cwd(cwd))
    return keys


def normalise_cwd(cwd: str) -> str:
    """One key per project, whichever slash and case the agent reported.

    Built outside an f-string on purpose: a backslash in an f-string expression
    is a syntax error before Python 3.12, and this package supports 3.11."""
    return str(cwd).replace("\\", "/").rstrip("/").lower()


def pinned(session_id: str, cwd: str = "", home: Path | None = None) -> dict:
    pins = load(home)
    for key in keys_for(session_id, cwd):
        if key in pins:
            # Dismissal belongs to an exact task, never every future task in its project.
            return {field: value for field, value in pins[key].items()
                    if field != "dismissed_status" or key.startswith("id:")}
    return {}


def remember(session_id: str, cwd: str = "", home: Path | None = None, now: float | None = None, **fields) -> None:
    """Pin a slot and/or a label for this session and its project."""
    now = now or time.time()
    with _lock:
        pins = load(home)
        for key in keys_for(session_id, cwd):
            pins[key] = {**pins.get(key, {}), **fields, "ts": now}
        save(pins, home, now)
