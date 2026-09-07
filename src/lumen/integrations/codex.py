"""OpenAI Codex (CLI, VS Code extension, desktop app) via its hooks, with a
hook-free fallback that reads the rollout files.

Hooks go into ~/.codex/hooks.json. Codex only runs hooks it trusts, so it may
ask you to approve them the first time. The fallback reads Codex's thread
database (~/.codex/state_5.sqlite) for recently touched threads and scans
each rollout's turn-boundary events (task_started / task_complete /
turn_aborted) incrementally; the rollout is authoritative over a hook file
stuck on running (an Esc-aborted turn fires no Stop).
"""

from __future__ import annotations

import re
import sqlite3
import threading
import time
from contextlib import closing
from pathlib import Path

from lumen.core.events import Event
from lumen.integrations import claude_usage, codex_usage
from lumen.integrations.agent_sessions import CODEX_HOOKS, AgentIntegration

CODEX_HOME = Path.home() / ".codex"
STATE_DB = CODEX_HOME / "state_5.sqlite"
ACTIVE_WINDOW_S = 10 * 60

TURN_OPEN_EVENTS = {b"task_started": True, b"user_message": True, b"task_complete": False, b"turn_aborted": False}
_TURN_EVENT_RE = re.compile(
    rb'"type":\s*"event_msg",\s*"payload":\s*\{\s*"type":\s*"(task_started|user_message|task_complete|turn_aborted)"'
)
_scan: dict[Path, tuple[int, bool]] = {}  # rollout -> (bytes consumed, turn open)


def turn_open(rollout: Path) -> bool:
    """Newest turn-boundary event wins. Rollouts grow to many MB inside one
    turn, so scan the whole file once and only the appended bytes afterwards."""
    offset, state = _scan.get(rollout, (0, False))
    with rollout.open("rb") as f:
        f.seek(0, 2)
        size = f.tell()
        if size < offset:
            offset, state = 0, False  # truncated/rotated
        f.seek(offset)
        chunk = f.read(size - offset)
    end = chunk.rfind(b"\n") + 1  # only consume complete lines
    for m in _TURN_EVENT_RE.finditer(chunk, 0, end):
        state = TURN_OPEN_EVENTS[m.group(1)]
    _scan[rollout] = (offset + end, state)
    return state


def rollout_states(db: Path = STATE_DB, now: float | None = None, hooked=()) -> dict[str, bool]:
    """thread_id -> turn open, for recently touched threads plus every hooked
    one (no freshness cutoff there: a closed or archived rollout must keep
    overriding a stuck hook file)."""
    now = now or time.time()
    cutoff_ms = int((now - ACTIVE_WINDOW_S) * 1000)
    hooked = list(hooked)
    try:
        with closing(sqlite3.connect(f"file:{db}?mode=ro", uri=True)) as conn:
            rows = conn.execute(
                "select id, rollout_path, archived from threads where (archived = 0 and updated_at_ms > ?) or id in (%s)"
                % ",".join("?" * len(hooked)), (cutoff_ms, *hooked)
            ).fetchall()
    except sqlite3.Error:
        return {}
    out: dict[str, bool] = {}
    for tid, path, archived in rows:
        if archived:
            out[tid] = out.get(tid, False)
            continue
        rollout = Path(path)
        try:
            fresh = now - rollout.stat().st_mtime < ACTIVE_WINDOW_S
        except OSError:
            continue
        if fresh or tid in hooked:
            out[tid] = out.get(tid, False) or turn_open(rollout)
    return out


class Codex(AgentIntegration):
    id = "codex"
    agent = "codex"
    name = "Codex"
    description = "OpenAI's coding agent — CLI, VS Code extension, desktop app."
    events = ("agent.running", "agent.needs_input", "agent.finished", "agents.status", "codex.usage")
    hooks_file = CODEX_HOME / "hooks.json"
    hooks = CODEX_HOOKS
    hooks_async = False  # Codex skips hooks marked async
    docs = ("Connect writes the hook command to `~/.codex/hooks.json`; Codex asks you to trust it on its "
            "next start. Without hooks, Lumen reads the rollout files to tell busy from idle, but cannot "
            "see requests for input.")

    def truth(self, hooked: dict[str, str]) -> dict[str, bool]:
        return rollout_states(hooked=list(hooked))

    def start(self) -> None:
        super().start()
        threading.Thread(target=self._usage_loop, name="lumen-codex-usage", daemon=True).start()

    def _usage_loop(self) -> None:
        """The 5-hour / weekly limits, every few minutes, as a codex.usage event when they move."""
        last = None
        while not self._stop.is_set():
            try:
                usage = codex_usage.refresh()
            except Exception as e:  # never let the usage poll take the session poll down
                usage = None
                print(f"codex: usage poll failed: {type(e).__name__}: {e}", flush=True)
            if usage is not None and usage != last:
                last = usage
                self.emit(Event("codex.usage", self.agent,
                                {"agent": self.agent, **usage, **claude_usage.flat(usage)}))
            self._stop.wait(codex_usage.POLL_S)

    def status(self) -> dict:
        out = super().status()
        usage = codex_usage.latest()
        if usage:
            parts = [f"{name} {usage[k]['used']}%" for k, name in (("five_hour", "5h"), ("seven_day", "7d")) if k in usage]
            out["detail"] += " · " + " · ".join(parts)
        out["usage"] = usage
        return out


INTEGRATION = Codex
