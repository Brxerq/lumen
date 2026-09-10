"""Google & Gemini integration — tracks Gemini CLI, Antigravity, and Google AI Studio sessions
and quota limits.
"""

from __future__ import annotations

import os
import re
import sqlite3
import sys
import threading
from pathlib import Path
from collections.abc import Callable

from lumen.core.events import Event
from lumen.integrations import google_usage
from lumen.integrations.agent_sessions import AgentIntegration, RUNNING, INPUT, DONE

GEMINI_HOME = Path.home() / ".gemini"

GEMINI_HOOKS = {
    "SessionStart": None,
    "UserPromptSubmit": None,
    "PostToolUse": None,
    "PermissionRequest": None,
    "Stop": None,
    "SessionEnd": None,
    "PreToolUse": "request_user_input|AskUserQuestion",
}


def antigravity_states(home: Path | None = None, metadata: dict | None = None) -> dict[str, bool]:
    """Scan local Antigravity conversation databases and return {conv_id: is_running}
    while populating metadata dict if provided."""
    base = (home or GEMINI_HOME) / "antigravity"
    conv_dir = base / "conversations"
    if not conv_dir.is_dir():
        return {}
    out: dict[str, bool] = {}
    for db in conv_dir.glob("*.db"):
        conv_id = db.stem
        try:
            mtime = db.stat().st_mtime
            # Read sqlite DB read-only
            conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            with conn:
                row = conn.execute("SELECT idx, step_type, status FROM steps ORDER BY idx DESC LIMIT 1").fetchone()
            if not row:
                continue
            # status 2 = in progress / running; status 1 = waiting for input / tool approval; status 3 = done
            is_running = (row[2] == 2)
            is_input = (row[2] == 1)
            out[conv_id] = is_running

            title = ""
            pbtxt = base / "annotations" / f"{conv_id}.pbtxt"
            if pbtxt.is_file():
                try:
                    txt = pbtxt.read_text(encoding="utf-8", errors="ignore")
                    m = re.search(r'title:\s*"([^"]+)"', txt)
                    if m:
                        title = m.group(1)
                except Exception:
                    pass

            if metadata is not None:
                metadata[conv_id] = {
                    "cwd": "Antigravity",
                    "title": title or f"Antigravity ({conv_id[:8]})",
                    "ts": mtime,
                    "tracking_health": "ok",
                    "status": RUNNING if is_running else (INPUT if is_input else DONE),
                }
        except Exception:
            continue
    return out


class GeminiIntegration(AgentIntegration):
    id = "gemini"
    agent = "gemini"
    name = "Google & Gemini"
    description = "Tracks Google Antigravity & Gemini CLI turns, sessions, and quota limits."
    events = (
        "agent.running", "agent.needs_input", "agent.finished",
        "agent.session.running", "agent.session.needs_input", "agent.session.finished",
        "agents.status", "google.usage", "gemini.usage",
    )
    hooks_file = GEMINI_HOME / "settings.json"
    hooks = GEMINI_HOOKS
    docs = "Reads local Antigravity conversation states and Google/Gemini subscription & quota limits."

    def __init__(self, emit: Callable[[Event], None] | None = None, options: dict | None = None):
        super().__init__(emit or (lambda e: None), options or {})
        self._usage_thread: threading.Thread | None = None

    def truth(self, hooked: dict[str, str]) -> dict[str, bool]:
        records: dict[str, dict] = {}
        states = antigravity_states(metadata=records)
        self._fallback_records = {sid: {**d, "agent": self.agent, "id": sid} for sid, d in records.items()}
        return states

    def start(self) -> None:
        super().start()
        self._stop.clear()
        self._usage_thread = threading.Thread(target=self._usage_loop, name="lumen-gemini-usage", daemon=True)
        self._usage_thread.start()

    def _usage_loop(self) -> None:
        last = None
        while not self._stop.is_set():
            try:
                usage = google_usage.refresh()
            except Exception as e:
                usage = None
                print(f"google: usage poll failed: {type(e).__name__}: {e}", flush=True)
            if usage is not None and usage != last:
                last = usage
                payload = {"agent": self.agent, **usage, **google_usage.flat(usage)}
                self.emit(Event("google.usage", self.agent, payload))
                self.emit(Event("gemini.usage", self.agent, payload))
            self._stop.wait(google_usage.POLL_S)

    def status(self) -> dict:
        out = super().status()
        usage = google_usage.latest()
        if usage:
            parts = [f"{k.replace('gemini_', '')} {usage[k]['used']}%" for k in google_usage.ordered(usage)]
            out["detail"] = (out.get("detail") or "") + " · " + " · ".join(parts)
        else:
            out["detail"] = (out.get("detail") or "") + f" · limits · {google_usage.detail()}"
        out["usage"] = usage
        return out


INTEGRATION = GeminiIntegration
