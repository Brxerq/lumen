"""Shared machinery for AI coding agents (Claude Code, Codex): the hook
command, the per-session state files, and the tracker that folds sessions
into events.

Both agents run the same hook command on their lifecycle events:

    lumen hook        (stdin: the agent's JSON payload)

Each call writes <data dir>/sessions/<session_id>.json with a status:

    running  a turn is open
    input    the agent is waiting on you (permission prompt, question)
    done     the turn finished

The tracker folds every live session of one agent into one status
(input > running > done), lets the agent's own record (transcript / rollout)
override hooks that missed a Stop, and emits events on every change:

    agent.running / agent.needs_input / agent.finished   {"agent": name}
    agents.status                                        {"status": ...}  (all agents folded)
    agents.sessions                                      {"status": ..., "sessions": [{id, agent, status, slot, ...}]}

Each live session gets a sticky *slot* (0, 1, 2...): the `sessions` effect
paints slot N onto zone N of a multi-zone device, so four Claude tabs are four
keyboard zones, each in its own status color.

The hook side must stay stdlib-only and fast: it runs on every tool call.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from lumen import paths
from lumen.core import slots
from lumen.core.events import Event
from lumen.core.integrations import Integration

RUNNING, INPUT, DONE = "running", "input", "done"
PRIORITY = (INPUT, RUNNING, DONE)
STALE_AFTER_S = 4 * 3600  # a session whose hooks went silent this long is gone
GONE_GRACE_S = 120        # ...or this long, once the agent's own record has dropped it

# hook_event_name -> status. SessionEnd maps to None: forget the session.
HOOK_EVENTS = {
    "SessionStart": DONE,
    "UserPromptSubmit": RUNNING,
    "PostToolUse": RUNNING,
    "PreToolUse": INPUT,          # only reaches us via matcher: AskUserQuestion / request_user_input
    "PermissionRequest": INPUT,
    "Notification": INPUT,        # matcher: permission_prompt|elicitation_dialog
    "Stop": DONE,
    "SessionEnd": None,
}
# Which hook events each agent needs, and the matcher (None = every call).
CLAUDE_HOOKS = {"SessionStart": None, "UserPromptSubmit": None, "PostToolUse": None, "PermissionRequest": None,
                "Stop": None, "SessionEnd": None, "PreToolUse": "AskUserQuestion",
                "Notification": "permission_prompt|elicitation_dialog"}
CODEX_HOOKS = {"SessionStart": None, "UserPromptSubmit": None, "PostToolUse": None, "PermissionRequest": None,
               "Stop": None, "SessionEnd": None, "PreToolUse": "request_user_input"}
# Only the exact shapes hook_command() can produce — quoting the executable is
# what agents write, so anything looser would delete other people's hooks that
# merely mention lumen and hook.
_OUR_HOOK = re.compile(
    r"^(?:"
    r"lumen(?:\.exe)? hook"                   # `lumen hook` found on PATH
    r'|"[^"]*[\\/]?[Ll]umen(?:\.exe)?" hook'  # "<dir>/lumen.exe" hook (frozen install)
    r'|(?:"[^"]+"|\S+) -m lumen hook'         # <python> -m lumen hook, quoted or not (source install)
    r")$"
)


def _safe_session_id(value) -> str | None:
    """Session ids end up as file names: never let one escape the state dir."""
    text = str(value or "")
    if not text or not text.replace("-", "").replace("_", "").isalnum():
        return None
    return text


# ---------------------------------------------------------------------------
# Hook side
# ---------------------------------------------------------------------------

def agent_of(payload: dict) -> str:
    # Codex payloads carry turn_id and a ~/.codex transcript; Claude Code's don't.
    if "turn_id" in payload or ".codex" in str(payload.get("transcript_path") or ""):
        return "codex"
    return "claude"


def apply_hook(payload: dict, state_dir: Path | None = None, now: float | None = None) -> str | None:
    """Record one hook event. Returns the status written (None = session forgotten)."""
    state_dir = state_dir or paths.sessions_dir()
    event = payload.get("hook_event_name")
    session_id = _safe_session_id(payload.get("session_id"))
    if event not in HOOK_EVENTS or session_id is None:
        return None
    status = HOOK_EVENTS[event]
    path = state_dir / f"{session_id}.json"
    if status is None:
        path.unlink(missing_ok=True)
        return None
    if event == "SessionStart" and not path.exists():
        return None  # a tab that never sent a prompt takes no slot (helper/one-shot sessions never do)
    state_dir.mkdir(parents=True, exist_ok=True)
    now = now or time.time()
    try:  # keep what the previous hook learned: slot order, running token totals
        previous = json.loads(path.read_text())
        previous = previous if isinstance(previous, dict) else {}
    except (OSError, ValueError):
        previous = {}
    record = {"status": status, "ts": now, "agent": agent_of(payload),
              "started": previous.get("started") or now}
    for key in ("tokens", "model", "transcript_offset"):
        if key in previous:
            record[key] = previous[key]
    record["activity"] = activity_of(payload, previous.get("activity", ""))
    if payload.get("cwd"):
        record["cwd"] = str(payload["cwd"])
    if payload.get("transcript_path"):
        record["context"] = context_usage(Path(str(payload["transcript_path"])), record)
    path.write_text(json.dumps(record))
    return status


# tool_name -> how to say what the agent is doing, in the two or three words a
# dashboard row (or a zone tooltip) has space for.
def activity_of(payload: dict, previous: str = "") -> str:
    """A short human phrase for what this hook call means.

    Hooks that carry no tool information (Notification, PermissionRequest,
    SessionStart) keep whatever the last one said: they interrupt an activity,
    they don't replace it. Only Stop clears it — the turn is over."""
    tool = str(payload.get("tool_name") or "")
    if tool:
        args = payload.get("tool_input")
        args = args if isinstance(args, dict) else {}
        name = Path(str(args.get("file_path") or "")).name
        if tool in ("Edit", "Write", "NotebookEdit", "MultiEdit"):
            return f"Editing {name}" if name else "Editing"
        if tool == "Read":
            return f"Reading {name}" if name else "Reading"
        if tool == "Bash":
            command = " ".join(str(args.get("command") or "").split())
            return f"Running: {command[:40]}" if command else "Running a command"
        if tool in ("Grep", "Glob", "WebSearch"):
            return "Searching"
        if tool in ("Agent", "Task"):
            return "Delegating"
        if tool == "AskUserQuestion":
            return "Asking you"
        return f"Using {tool}"
    event = payload.get("hook_event_name")
    if event == "UserPromptSubmit":
        return "Thinking"
    if event == "Stop":
        return ""
    return previous


CONTEXT_WINDOW = 200_000
_USAGE_TAIL = 256 * 1024

# $ per million tokens: (input, output, cache write, cache read). Matched by
# substring against the model name the transcript records; an unknown model is
# priced as the most expensive one so a surprise never reads as cheap.
PRICES = {
    "haiku": (1.0, 5.0, 1.25, 0.1),
    "sonnet": (3.0, 15.0, 3.75, 0.3),
    "opus": (15.0, 75.0, 18.75, 1.5),
    "fable": (15.0, 75.0, 18.75, 1.5),
}
DEFAULT_PRICE = PRICES["opus"]


def session_cost_usd(record: dict) -> float:
    """What this session has spent at list prices, from its running token totals."""
    tokens = record.get("tokens") or {}
    model = str(record.get("model") or "").lower()
    price = next((p for name, p in PRICES.items() if name in model), DEFAULT_PRICE)
    try:
        return sum(float(tokens.get(k, 0)) * rate
                   for k, rate in zip(("in", "out", "cache_write", "cache_read"), price)) / 1e6
    except (TypeError, ValueError):
        return 0.0


def _accumulate_tokens(transcript: Path, record: dict) -> None:
    """Add the tokens billed since the last hook into record["tokens"].

    The whole transcript would be the honest way to total a session, but it can
    be tens of megabytes and this runs on every tool call, so each hook reads
    only the bytes appended since the last one and remembers where it stopped
    ("transcript_offset"). Partial trailing lines are left for next time."""
    offset = record.get("transcript_offset")
    offset = offset if isinstance(offset, int) and offset >= 0 else 0
    totals = dict(record.get("tokens") or {})
    try:
        with transcript.open("rb") as f:
            f.seek(0, 2)
            size = f.tell()
            if size < offset:  # truncated or a different session reusing the path
                offset, totals = 0, {}
            f.seek(offset)
            chunk = f.read(size - offset)
    except OSError:
        return
    end = chunk.rfind(b"\n") + 1
    for raw in chunk[:end].splitlines():
        if b'"usage"' not in raw:
            continue
        try:
            message = json.loads(raw).get("message") or {}
            usage = message.get("usage") or {}
            counts = {"in": int(usage.get("input_tokens", 0)), "out": int(usage.get("output_tokens", 0)),
                      "cache_write": int(usage.get("cache_creation_input_tokens", 0)),
                      "cache_read": int(usage.get("cache_read_input_tokens", 0))}
        except (ValueError, AttributeError, TypeError):
            continue
        for key, value in counts.items():
            totals[key] = int(totals.get(key, 0)) + value
        if message.get("model"):
            record["model"] = str(message["model"])
    record["tokens"] = totals
    record["transcript_offset"] = offset + end


def context_usage(transcript: Path, record: dict | None = None) -> dict | None:
    """How full the session's context window is, from the last assistant turn
    in a Claude Code transcript: {"tokens": n, "window": size}. None if the
    transcript has no usage yet (or isn't Claude's).

    With a `record`, it also folds the newly appended turns into that record's
    running totals and reports them as "input_tokens" / "output_tokens" —
    cumulative over the whole session, not the last turn."""
    if record is not None:
        _accumulate_tokens(transcript, record)
    try:
        with transcript.open("rb") as f:
            f.seek(max(0, transcript.stat().st_size - _USAGE_TAIL))
            lines = f.read().splitlines()
    except OSError:
        return None
    for raw in reversed(lines):
        if b'"usage"' not in raw:
            continue
        try:
            message = json.loads(raw).get("message") or {}
            usage = message.get("usage") or {}
            tokens = int(usage.get("input_tokens", 0)) + int(usage.get("cache_creation_input_tokens", 0))                 + int(usage.get("cache_read_input_tokens", 0))
        except (ValueError, AttributeError, TypeError):
            continue
        if tokens <= 0:
            continue
        window = 1_000_000 if "[1m]" in str(message.get("model", "")) else CONTEXT_WINDOW
        out = {"tokens": tokens, "window": window}
        if record is not None:
            totals = record.get("tokens") or {}
            out["input_tokens"] = int(totals.get("in", 0))
            out["output_tokens"] = int(totals.get("out", 0))
        return out
    return None


def context_percent(session: dict) -> int | None:
    ctx = session.get("context") or {}
    try:
        return max(0, min(100, round(100 * int(ctx["tokens"]) / int(ctx["window"]))))
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return None


def run_hook() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        return 0  # never fail the agent over a bad payload
    try:
        apply_hook(payload)
    except OSError:
        pass
    return 0


def hook_command() -> str:
    """The command agents should run: the frozen exe, `lumen` on PATH, or this python."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" hook'
    on_path = shutil.which("lumen")
    if on_path:
        return f'"{on_path}" hook'
    return f'"{sys.executable}" -m lumen hook'


def install_hooks(settings_path: Path, hooks: dict[str, str | None], command: str | None = None,
                  async_: bool = True) -> str:
    """Merge our hook command into a Claude-style hooks file (idempotent).
    Other people's hooks are kept; older Lumen entries are replaced."""
    command = command or hook_command()
    data = _read_json(settings_path, strict=True)
    data.setdefault("hooks", {})
    _strip_ours(data["hooks"])
    for event, matcher in hooks.items():
        entry = {"type": "command", "command": command, "timeout": 5}
        if async_:
            entry["async"] = True
        group: dict[str, Any] = {"hooks": [entry]}
        if matcher:
            group["matcher"] = matcher
        data["hooks"].setdefault(event, []).append(group)
    _write_json(settings_path, data)
    return command


def uninstall_hooks(settings_path: Path) -> bool:
    data = _read_json(settings_path, strict=True)
    if "hooks" not in data:
        return False
    removed = _strip_ours(data["hooks"])
    if removed:
        _write_json(settings_path, data)
    return removed


def hooks_installed(settings_path: Path) -> bool:
    data = _read_json(settings_path)
    return any(_OUR_HOOK.search(h.get("command", "")) for groups in data.get("hooks", {}).values()
               for g in groups for h in g.get("hooks", []))


def _strip_ours(hooks: dict) -> bool:
    removed = False
    for event in list(hooks):
        kept = []
        for group in hooks[event]:
            inner = [h for h in group.get("hooks", []) if not _OUR_HOOK.search(h.get("command", ""))]
            removed |= len(inner) != len(group.get("hooks", []))
            if inner:
                kept.append({**group, "hooks": inner})
        if kept:
            hooks[event] = kept
        else:
            del hooks[event]
    return removed


def _read_json(path: Path, strict: bool = False) -> dict:
    """Read a settings file. `strict` refuses to treat an unreadable-but-present
    file as empty: overwriting someone's hand-edited settings.json because of a
    stray comma would be unforgivable."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    if not text.strip():
        return {}
    try:
        data = json.loads(text)
    except ValueError as e:
        if strict:
            raise ValueError(f"{path} is not valid JSON ({e}); fix it and try again") from None
        return {}
    return data if isinstance(data, dict) else {}


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    backup = path.with_name(path.name + ".bak-lumen")
    if path.exists() and not backup.exists():
        shutil.copy2(path, backup)
    mode = path.stat().st_mode if path.exists() else None
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}-", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(json.dumps(data, indent=2) + "\n")
    if mode is not None:  # mkstemp is 0600; keep whatever the user's file had
        os.chmod(tmp, mode)
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# Daemon side
# ---------------------------------------------------------------------------

def read_sessions(state_dir: Path | None = None, now: float | None = None) -> dict[str, dict]:
    """session_id -> record for every live session file."""
    state_dir = state_dir or paths.sessions_dir()
    now = now or time.time()
    out = {}
    for path in state_dir.glob("*.json"):
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue  # hook mid-write; next tick sees it
        if not isinstance(data, dict) or data.get("status") not in PRIORITY:
            continue
        if now - data.get("ts", 0) >= STALE_AFTER_S:
            path.unlink(missing_ok=True)  # hooks went silent hours ago: stop re-reading it every tick
            continue
        out[path.stem] = data
    return out


def forget_session(session_id: str, state_dir: Path | None = None) -> bool:
    """Drop a session file (dashboard "dismiss" for a tab that never sent SessionEnd)."""
    path = (state_dir or paths.sessions_dir()) / f"{session_id}.json"
    if not path.is_file():
        return False
    path.unlink(missing_ok=True)
    return True


def aggregate(statuses) -> str:
    statuses = list(statuses)  # a generator would be spent by the first `in`
    for status in PRIORITY:
        if status in statuses:
            return status
    return DONE


def session_statuses(hooked: dict[str, str], truth: dict[str, bool]) -> dict[str, str]:
    """session_id -> status. The agent's own record (transcript / rollout) is
    the authority on open/closed: hooks are async, can lose the Stop-vs-PostToolUse
    race, and an aborted turn (Esc) fires no Stop at all. Hooks still supply
    `input`, which the record can't see."""
    out = {}
    for sid in hooked.keys() | truth.keys():
        if sid in truth:
            out[sid] = (INPUT if hooked.get(sid) == INPUT else RUNNING) if truth[sid] else DONE
        else:
            out[sid] = hooked[sid]
    return out


def prune_gone(statuses: dict[str, str], truth: dict[str, bool], records: dict[str, dict],
               now: float | None = None, grace_s: float = GONE_GRACE_S) -> dict[str, str]:
    """Drop tabs that closed without a SessionEnd hook.

    Their hook file lingers for STALE_AFTER_S, holding a keyboard zone lit for
    hours. The agent's own record knows the session is gone long before that:
    Claude Code lists only sessions whose process is alive, and Codex lists
    every thread we ask about. So a hooked session the record does not mention
    is finished — but only once the record is readable at all (an empty `truth`
    means we have none to compare against) and only after a grace window, since
    a brand-new tab reaches our hook before it reaches the agent's own files."""
    if not truth:
        return statuses
    now = now or time.time()
    return {sid: status for sid, status in statuses.items()
            if sid in truth or now - (records.get(sid, {}).get("ts") or now) < grace_s}


_latest: dict[str, str] = {}        # agent -> last status, for the folded agents.status event
_latest_lock = threading.Lock()
_TRANSITION = {RUNNING: "agent.running", INPUT: "agent.needs_input", DONE: "agent.finished"}

# Every live session of every agent, with a sticky slot: a session keeps its
# slot (= keyboard zone) until it ends; a new one opens on zone 0, pushing the
# others along, unless it has a pinned zone of its own.
_sessions: dict[str, dict] = {}
_snapshot: list[dict] = []


def all_sessions() -> list[dict]:
    with _latest_lock:
        return [dict(s) for s in _snapshot]


def _update_sessions(agent: str, statuses: dict[str, str], records: dict[str, dict]) -> list[dict] | None:
    """Merge one agent's sessions into the table. Returns the new snapshot if it changed."""
    global _snapshot
    with _latest_lock:
        for sid in [s for s, v in _sessions.items() if v["agent"] == agent and s not in statuses]:
            del _sessions[sid]
        taken = {v["slot"] for v in _sessions.values()}
        first_seen = lambda s: records.get(s, {}).get("started")
        # A session we only know from the agent's own record (no hook file) takes a
        # zone once it does something: Claude Desktop keeps a process alive for
        # every tab you ever opened, and 28 idle transcripts would fill every zone
        # and push the tab that is actually working off the keyboard. Once seated,
        # it keeps its zone until the process is gone, like any other session.
        new = sorted((s for s in set(statuses) - set(_sessions) if s in records or statuses[s] != DONE),
                     key=lambda s: (float("inf") if first_seen(s) is None else first_seen(s), s))
        for sid in new:
            pin = slots.pinned(sid, records.get(sid, {}).get("cwd", ""))
            slot = pin.get("slot")
            if not isinstance(slot, int) or slot in taken:
                # An unpinned tab opens on the first zone and pushes the rest
                # along: with more tabs than zones, the one you just started is
                # the one you want to see.
                for other in _sessions.values():
                    other["slot"] += 1
                taken = {v["slot"] for v in _sessions.values()}
                slot = 0
            taken.add(slot)
            _sessions[sid] = {"id": sid, "agent": agent, "slot": slot, "label": pin.get("label", "")}
        for sid, status in statuses.items():
            if sid not in _sessions:
                continue
            r = records.get(sid, {})
            _sessions[sid].update(status=status, started=r.get("started"), ts=r.get("ts"), cwd=r.get("cwd", ""),
                                  context=r.get("context"), activity=r.get("activity", ""),
                                  tokens=r.get("tokens"), model=r.get("model", ""),
                                  cost_usd=round(session_cost_usd(r), 2))
        before = _key(_snapshot)
        snapshot = _rebuild()
        return snapshot if _key(snapshot) != before else None


def _key(snapshot: list[dict]) -> list[tuple]:
    """What counts as a change worth an event — timestamps alone do not, a
    context window that moved by another five percent does. Cost is bucketed to
    whole dollars: it creeps up on every tool call, and a repaint per cent
    would be an event storm for a number nobody watches that closely."""
    return [(s["id"], s["agent"], s["slot"], s["status"], s.get("label", ""), (context_percent(s) or 0) // 5,
             s.get("activity", ""), int(s.get("cost_usd") or 0))
            for s in snapshot]


def _rebuild() -> list[dict]:
    """Refresh the published snapshot from the table. Caller holds the lock."""
    global _snapshot
    _snapshot = sorted((dict(s) for s in _sessions.values()), key=lambda s: s["slot"])
    return _snapshot


MAX_SLOT = 31  # no keyboard has more zones than this; a bigger number is a typo


def set_slot(session_id: str, slot: int) -> list[dict]:
    """Move a session to a zone, swapping with whoever is there.

    Swapping (rather than shifting everyone along) keeps every other tab where
    the user last put it, which is the whole point of arranging them by hand.
    The new positions are pinned, so they survive the tab and the daemon."""
    if not isinstance(slot, int) or not 0 <= slot <= MAX_SLOT:
        raise ValueError(f"slot must be between 0 and {MAX_SLOT}")
    with _latest_lock:
        session = _sessions.get(session_id)
        if session is None:
            raise ValueError("no such session")
        occupant = next((s for s in _sessions.values() if s["slot"] == slot and s["id"] != session_id), None)
        if occupant is not None:
            occupant["slot"] = session["slot"]
        session["slot"] = slot
        moved = [dict(s) for s in (session, occupant) if s is not None]
        _rebuild()
    for s in moved:
        slots.remember(s["id"], s.get("cwd", ""), slot=s["slot"])
    return moved


def set_order(session_ids: list[str]) -> list[dict]:
    """Lay the named sessions out over the zones they already occupy, in the
    order given. Sessions not named keep their zone — that is what makes
    reordering one agent's tabs leave the other agent's alone."""
    with _latest_lock:
        wanted = [sid for sid in session_ids if sid in _sessions]
        if len(set(wanted)) != len(wanted):
            raise ValueError("duplicate session id")
        free = sorted(_sessions[sid]["slot"] for sid in wanted)
        for sid, slot in zip(wanted, free):
            _sessions[sid]["slot"] = slot
        moved = [dict(_sessions[sid]) for sid in wanted]
        _rebuild()
    for s in moved:
        slots.remember(s["id"], s.get("cwd", ""), slot=s["slot"])
    return moved


def set_label(session_id: str, label: str) -> dict:
    """Name a tab ("API refactor") so its zone means something."""
    label = str(label)[:60].strip()
    with _latest_lock:
        session = _sessions.get(session_id)
        if session is None:
            raise ValueError("no such session")
        session["label"] = label
        out = dict(session)
        _rebuild()
    slots.remember(session_id, out.get("cwd", ""), label=label)
    return out


class AgentIntegration(Integration):
    """Polls session files (+ the agent's own record) and emits transitions.
    Subclasses provide `agent`, `truth()` and the hook installer bits."""

    agent = "agent"
    hooks_file: Path = Path()
    hooks: dict[str, str | None] = {}
    hooks_async = True
    poll_interval_s = 0.5
    can_connect = True

    def __init__(self, emit: Callable[[Event], None], options: dict):
        super().__init__(emit, options)
        self._stop = threading.Event()
        self._status: str | None = None

    def truth(self, hooked: dict[str, str]) -> dict[str, bool]:
        """session_id -> turn open, from the agent's own files. Override."""
        return {}

    def current_sessions(self) -> dict[str, str]:
        """session_id -> status for this agent's live sessions."""
        self._records = read_sessions()
        hooked = {sid: r["status"] for sid, r in self._records.items() if r.get("agent", "claude") == self.agent}
        try:
            truth = self.truth(hooked)
        except Exception:
            truth = {}
        statuses = prune_gone(session_statuses(hooked, truth), truth, self._records)
        for sid in set(hooked) - set(statuses):  # tab is gone: stop reading its file every tick
            forget_session(sid)
        return statuses

    @property
    def sessions(self) -> list[dict]:
        return [s for s in all_sessions() if s["agent"] == self.agent]

    def start(self) -> None:
        threading.Thread(target=self._loop, name=f"lumen-{self.agent}", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._poll()
            except Exception as e:
                print(f"{self.agent}: poll failed: {type(e).__name__}: {e}", flush=True)
            self._stop.wait(self.poll_interval_s)

    def _poll(self) -> None:
        statuses = self.current_sessions()
        first = self._status is None
        self._poll_status(aggregate(statuses.values()))
        snapshot = _update_sessions(self.agent, statuses, getattr(self, "_records", {}))
        if first and snapshot is None:
            snapshot = all_sessions()  # nothing open yet: still settle the zones (idle color) at startup
        if snapshot is not None:  # after agents.status, so per-zone rules win on the same device
            self.emit(Event("agents.sessions", self.agent,
                            {"status": aggregate(s["status"] for s in snapshot), "sessions": snapshot}))

    def _poll_status(self, status: str) -> None:
        first = self._status is None
        if status == self._status:
            return
        self._status = status
        if not first:  # don't flash on daemon start; just settle the base colors
            self.emit(Event(_TRANSITION[status], self.agent, {"agent": self.agent, "status": status}))
        with _latest_lock:
            before = aggregate(_latest.values())
            nobody_yet = not _latest
            _latest[self.agent] = status
            after = aggregate(_latest.values())
        if before != after or (first and nobody_yet):  # settle the base colors once at startup
            self.emit(Event("agents.status", self.agent, {"status": after, "agents": dict(_latest)}))

    # --- dashboard -------------------------------------------------------------
    def status(self) -> dict:
        installed = hooks_installed(self.hooks_file)
        state = self._status or "unknown"
        return {"connected": installed, "state": state,
                "detail": f"hooks installed · {state}" if installed else "hooks not installed (fallback detection only)"}

    def connect(self) -> str:
        try:
            cmd = install_hooks(self.hooks_file, self.hooks, async_=self.hooks_async)
        except ValueError as e:
            return str(e)
        return f"Hooks installed in {self.hooks_file} (command: {cmd}). Sessions started from now on report their status."

    def disconnect(self) -> str:
        try:
            removed = uninstall_hooks(self.hooks_file)
        except ValueError as e:
            return str(e)
        return "Hooks removed." if removed else "No Lumen hooks were installed."
