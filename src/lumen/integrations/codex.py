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

import json
import os
import sqlite3
import threading
import time
from contextlib import closing
from pathlib import Path

from lumen.core.events import Event
from lumen.integrations import claude_usage, codex_usage
from lumen.integrations.agent_sessions import CODEX_HOOKS, AgentIntegration

CODEX_HOME = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
STATE_DB = CODEX_HOME / "state_5.sqlite"
ACTIVE_WINDOW_S = 10 * 60

TURN_OPEN_EVENTS = {"task_started": True, "user_message": True, "task_complete": False, "turn_aborted": False}
_scan: dict[Path, tuple[int, bool, bool, bool]] = {}  # rollout -> (bytes consumed, turn open, saw metadata, belongs)


class TrackingUnavailable(RuntimeError):
    """The local Codex database could not be read this poll."""


def _is_internal(source: object) -> bool:
    """Codex stores workers and approval guardians as ordinary threads.

    They are useful implementation details but are not user-opened tabs. Keep
    parsing deliberately narrow: unknown sources remain visible.
    """
    text = str(source or "").lower()
    return '"subagent"' in text or '"guardian"' in text


def _display_title(value: object) -> str:
    """A bounded, single-line local title; never expose an entire prompt."""
    return " ".join(str(value or "").split())[:80]


def turn_open(rollout: Path, session_id: str | None = None) -> bool:
    """Newest turn-boundary event wins. Rollouts grow to many MB inside one
    turn, so scan the whole file once and only the appended bytes afterwards."""
    offset, state, has_metadata, belongs = _scan.get(rollout, (0, False, False, True))
    with rollout.open("rb") as f:
        f.seek(0, 2)
        size = f.tell()
        if size < offset:
            offset, state, has_metadata, belongs = 0, False, False, True  # truncated/rotated
        f.seek(offset)
        chunk = f.read(size - offset)
    end = chunk.rfind(b"\n") + 1  # only consume complete lines
    if not has_metadata:
        belongs = True
    for raw in chunk[:end].splitlines():
        try:
            entry = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if not isinstance(entry, dict):
            continue
        payload = entry.get("payload")
        payload = payload if isinstance(payload, dict) else {}
        if entry.get("type") == "session_meta":
            has_metadata = True
            belongs = session_id is None or str(payload.get("id") or payload.get("session_id") or "") == session_id
            continue
        if entry.get("type") != "event_msg" or not belongs:
            continue
        boundary = payload.get("type")
        if boundary in TURN_OPEN_EVENTS:
            state = TURN_OPEN_EVENTS[boundary]
    _scan[rollout] = (offset + end, state, has_metadata, belongs)
    return state


def rollout_states(db: Path = STATE_DB, now: float | None = None, hooked=(), metadata: dict[str, dict] | None = None) -> dict[str, bool]:
    """thread_id -> turn open, for recently touched threads plus every hooked
    one (no freshness cutoff there: a closed or archived rollout must keep
    overriding a stuck hook file)."""
    now = now or time.time()
    cutoff_ms = int((now - ACTIVE_WINDOW_S) * 1000)
    hooked = list(hooked)
    if not db.is_file():
        return {}
    try:
        with closing(sqlite3.connect(f"file:{db}?mode=ro", uri=True)) as conn:
            columns = {row[1] for row in conn.execute("pragma table_info(threads)")}
            required = {"id", "rollout_path", "archived", "updated_at_ms"}
            if not required <= columns:
                raise TrackingUnavailable("unsupported Codex thread database schema")
            optional = lambda name: name if name in columns else f"NULL as {name}"
            rows = conn.execute(
                f"select id, rollout_path, archived, updated_at_ms, {optional('cwd')}, {optional('title')}, {optional('source')} from threads "
                "where (archived = 0 and updated_at_ms > ?) or id in (%s)"
                % ",".join("?" * len(hooked)), (cutoff_ms, *hooked)
            ).fetchall()
    except TrackingUnavailable:
        raise
    except sqlite3.Error as e:
        raise TrackingUnavailable(str(e)) from e
    out: dict[str, bool] = {}
    rollout_unavailable = False
    for tid, path, archived, updated_ms, cwd, title, source in rows:
        if _is_internal(source):
            continue
        if archived:
            out[tid] = out.get(tid, False)
            continue
        rollout = Path(path)
        try:
            state = turn_open(rollout, str(tid))
        except OSError:
            # A file can disappear between SQLite's record and our read. It is
            # not a completion signal; let the tracker retain the last state.
            rollout_unavailable = True
            continue
        out[tid] = out.get(tid, False) or state
        if metadata is not None:
            metadata[str(tid)] = {"started": updated_ms / 1000 if updated_ms else None,
                                  "ts": updated_ms / 1000 if updated_ms else None,
                                  "cwd": str(cwd or ""), "title": _display_title(title),
                                  "source": str(source or ""), "tracking_health": "ok"}
    if rollout_unavailable:
        raise TrackingUnavailable("one or more Codex rollouts were unavailable")
    return out


class Codex(AgentIntegration):
    id = "codex"
    agent = "codex"
    name = "Codex"
    description = "OpenAI's coding agent — CLI, VS Code extension, desktop app."
    events = ("agent.running", "agent.needs_input", "agent.finished", "agent.session.running", "agent.session.needs_input",
              "agent.session.finished", "agents.status", "codex.usage")
    hooks_file = CODEX_HOME / "hooks.json"
    hooks = CODEX_HOOKS
    hooks_async = False  # Codex skips hooks marked async
    docs = ("Connect writes the hook command to `~/.codex/hooks.json`; Codex asks you to trust it on its "
            "next start. Without hooks, Lumen reads the rollout files to tell busy from idle, but cannot "
            "see requests for input.")

    def truth(self, hooked: dict[str, str]) -> dict[str, bool]:
        self._fallback_records = {}
        known = {s["id"] for s in self.sessions}
        # A quiet tool/delegated turn can stop updating both the thread row and
        # rollout for several minutes. Once observed, keep checking its exact
        # ID until Codex gives us a terminal/archived record.
        return rollout_states(hooked=list(set(hooked) | known), metadata=self._fallback_records)

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
            short = {"five_hour": "5h", "seven_day": "7d"}
            parts = [f"{short.get(k, k)} {usage[k]['used']}%" for k in claude_usage.ordered(usage)]
            out["detail"] += " · " + " · ".join(parts)
        else:
            # No numbers rather than old ones: say why, so the row does not just
            # go blank when the login expires.
            out["detail"] += " · limits unavailable — " + codex_usage.detail()
        out["usage"] = usage
        return out


INTEGRATION = Codex
