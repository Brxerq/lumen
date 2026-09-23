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

import copy
import json
import threading
import time
from pathlib import Path

from lumen import paths

FORGET_AFTER_S = 7 * 24 * 3600
_lock = threading.Lock()


def _file(home: Path | None = None) -> Path:
    return (home or paths.data_dir()) / "slots.json"


_cache: dict[Path, tuple[tuple[int, int], dict]] = {}


def load(home: Path | None = None) -> dict[str, dict]:
    """Every poll of every agent asks for pins, twice a second. Reread only when
    the file changed: on Windows each open reader also blocks the next save."""
    path = _file(home)
    try:
        st = path.stat()
        stamp = (st.st_mtime_ns, st.st_size)
        hit = _cache.get(path)
        if hit is None or hit[0] != stamp:
            data = json.loads(path.read_text(encoding="utf-8"))
            _cache[path] = hit = (stamp, data if isinstance(data, dict) else {})
    except (OSError, ValueError):
        return {}
    return copy.deepcopy(hit[1])  # callers edit what they get


def save(pins: dict[str, dict], home: Path | None = None, now: float | None = None) -> None:
    now = now or time.time()
    fresh = {k: v for k, v in pins.items() if now - v.get("ts", now) < FORGET_AFTER_S}
    paths.write_atomic(_file(home), json.dumps(fresh, indent=2))


def keys_for(session_id: str, cwd: str = "") -> list[str]:
    """Where a session's pin might be found, most specific first."""
    keys = [f"id:{session_id}"]
    # "Antigravity" is a label, not a project: one key for it would give every
    # new conversation the last one's zone and name.
    if cwd and cwd != "Antigravity":
        keys.append("cwd:" + normalise_cwd(cwd))
    return keys


def normalise_cwd(cwd: str) -> str:
    """One key per project, whichever slash and case the agent reported.

    Built outside an f-string on purpose: a backslash in an f-string expression
    is a syntax error before Python 3.12, and this package supports 3.11."""
    # Codex can report an extended-length path (the \\?\ prefix); unstripped,
    # its pins and Claude's never matched for the same project.
    return str(cwd).removeprefix("\\\\?\\").replace("\\", "/").rstrip("/").lower()


def pinned(session_id: str, cwd: str = "", home: Path | None = None) -> dict:
    pins = load(home)
    out: dict = {}
    # Most specific key wins per field, but fields merge: an id entry holding
    # only a dismissal must not hide the project's pinned zone.
    for key in reversed(keys_for(session_id, cwd)):
        if key in pins:
            # Dismissal belongs to an exact task, never every future task in its project.
            out.update({field: value for field, value in pins[key].items()
                        if field != "dismissed_status" or key.startswith("id:")})
    out.pop("ts", None)
    return out


def remember(session_id: str, cwd: str = "", home: Path | None = None, now: float | None = None, **fields) -> None:
    """Pin a slot and/or a label for this session and its project."""
    now = now or time.time()
    with _lock:
        pins = load(home)
        for key in keys_for(session_id, cwd):
            pins[key] = {**pins.get(key, {}), **fields, "ts": now}
        save(pins, home, now)
