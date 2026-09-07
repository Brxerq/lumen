"""Claude Code (CLI, VS Code, Cursor, JetBrains — anything that runs the Claude
Code agent) via its hooks, with a hook-free fallback.

Hooks are installed into ~/.claude/settings.json by the *Connect* button (or
`lumen connect claude`). The fallback needs nothing: every live `claude`
process records ~/.claude/sessions/<pid>.json (session id + cwd), and its
transcript at ~/.claude/projects/<cwd munged>/<session>.jsonl ends in an
assistant `end_turn` when idle. Sessions opened before the hooks existed are
caught this way, and a transcript that says "idle" overrides a hook file stuck
on running.
"""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path

from lumen.core.events import Event
from lumen.integrations import claude_usage
from lumen.integrations.agent_sessions import CLAUDE_HOOKS, AgentIntegration

CLAUDE_HOME = Path.home() / ".claude"
_TAIL_BYTES = 64 * 1024


def transcript_path(session: dict, home: Path = CLAUDE_HOME) -> Path:
    project = re.sub(r"[^A-Za-z0-9]", "-", session["cwd"])
    return home / "projects" / project / f"{session['sessionId']}.jsonl"


def turn_open(transcript: Path) -> bool:
    try:
        with transcript.open("rb") as f:
            f.seek(max(0, transcript.stat().st_size - _TAIL_BYTES))
            lines = f.read().splitlines()
    except OSError:
        return False
    for raw in reversed(lines):
        try:
            entry = json.loads(raw)
        except ValueError:
            continue
        if entry.get("type") == "assistant":
            return (entry.get("message") or {}).get("stop_reason") != "end_turn"
        if entry.get("type") == "user":
            return _opens_turn(entry)
    return False


def _opens_turn(entry: dict) -> bool:
    """Does this `user` entry mean a turn is running?

    Two shapes reach the transcript. A tool result carries no `origin` and is
    written mid-turn, so it always does. A prompt carries `origin.kind`, and
    only a human one does: Claude Code injects a `task-notification` prompt when
    a tab is reopened with background work unaccounted for, and if nobody
    focuses that tab it sits there unanswered. Counting it as an open turn is
    what made idle Claude Desktop tabs report "working" for hours and hold a
    keyboard zone amber.
    """
    origin = entry.get("origin")
    return not isinstance(origin, dict) or origin.get("kind") == "human"


def transcript_states(home: Path = CLAUDE_HOME) -> dict[str, bool]:
    """session_id -> turn open, for every live claude process with a transcript.
    No freshness cutoff: an idle transcript must keep overriding a stuck hook
    file however old it gets."""
    import psutil

    out = {}
    for path in (home / "sessions").glob("*.json"):
        try:
            session = json.loads(path.read_text())
            transcript = transcript_path(session, home)
        except (OSError, ValueError, KeyError):
            continue
        if transcript.exists() and psutil.pid_exists(session.get("pid", -1)):
            out[session["sessionId"]] = turn_open(transcript)
    return out


class ClaudeCode(AgentIntegration):
    id = "claude"
    agent = "claude"
    name = "Claude Code"
    description = "Anthropic's coding agent — CLI, VS Code, Cursor, JetBrains, desktop app."
    events = ("agent.running", "agent.needs_input", "agent.finished", "agents.status", "claude.usage")
    hooks_file = CLAUDE_HOME / "settings.json"
    hooks = CLAUDE_HOOKS
    docs = ("Connect installs a hook command in `~/.claude/settings.json` that reports each session's "
            "state. Without hooks, Lumen still reads the session transcripts to tell busy from idle, "
            "but cannot see permission prompts.")

    def truth(self, hooked: dict[str, str]) -> dict[str, bool]:
        return transcript_states()

    def start(self) -> None:
        super().start()
        threading.Thread(target=self._usage_loop, name="lumen-claude-usage", daemon=True).start()

    def _usage_loop(self) -> None:
        """The 5-hour / 7-day limits, every few minutes, as a claude.usage event when they move."""
        last = None
        while not self._stop.is_set():
            try:
                usage = claude_usage.refresh()
            except Exception as e:  # never let the usage poll take the session poll down
                usage = None
                print(f"claude: usage poll failed: {type(e).__name__}: {e}", flush=True)
            if usage is not None and usage != last:
                last = usage
                self.emit(Event("claude.usage", self.agent,
                                {"agent": self.agent, **usage, **claude_usage.flat(usage)}))
            self._stop.wait(claude_usage.POLL_S)

    def status(self) -> dict:
        out = super().status()
        usage = claude_usage.latest()
        if usage:
            parts = [f"{name} {usage[k]['used']}%" for k, name in (("five_hour", "5h"), ("seven_day", "7d")) if k in usage]
            out["detail"] += " · " + " · ".join(parts)
        out["usage"] = usage
        return out


INTEGRATION = ClaudeCode
