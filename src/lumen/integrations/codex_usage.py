"""Codex plan usage — the 5-hour and weekly limits the Codex CLI shows — read
with the login Codex already holds.

The access token sits in ~/.codex/auth.json, where Codex writes it. Lumen uses
it read-only against the same endpoint Codex queries and never refreshes it: a
rotated refresh token would sign Codex out, so an expired token just means "no
numbers until Codex signs in again".

    latest() -> {"five_hour": {"used": 23, "resets_at": 1788...}, "seven_day": {...}} or None

Same output shape as claude_usage, so the dashboard and the rules do not have
to care which agent the numbers came from.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from lumen.integrations.claude_usage import _when

USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
AUTH_FILE = Path.home() / ".codex" / "auth.json"
# response key -> ours. Codex names its windows by rank, not by length.
WINDOWS = {"primary": "five_hour", "secondary": "seven_day"}
POLL_S = 300

_lock = threading.Lock()
_latest: dict | None = None
_detail = "not checked yet"


def latest() -> dict | None:
    with _lock:
        return dict(_latest) if _latest else None


def detail() -> str:
    with _lock:
        return _detail


def credentials() -> dict | None:
    """{"access_token": ..., "account_id": ...} from Codex's auth file, or None."""
    try:
        data = json.loads(AUTH_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    tokens = data.get("tokens") if isinstance(data, dict) else None
    if not isinstance(tokens, dict) or not tokens.get("access_token"):
        return None
    return {"access_token": str(tokens["access_token"]), "account_id": str(tokens.get("account_id") or "")}


def fetch(token: str, account_id: str = "", timeout: float = 10) -> dict:
    req = urllib.request.Request(USAGE_URL, headers={
        "Authorization": f"Bearer {token}", "ChatGPT-Account-Id": account_id,
        "Accept": "application/json", "User-Agent": "lumen"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read().decode("utf-8", "replace"))
    return data if isinstance(data, dict) else {}


def _block(raw: dict, key: str) -> dict | None:
    """The window block, wherever this version of the endpoint put it: nested
    under "rate_limit", at the top level, with or without the "_window" suffix."""
    for holder in (raw.get("rate_limit"), raw.get("rate_limits"), raw):
        if not isinstance(holder, dict):
            continue
        for name in (f"{key}_window", key):
            block = holder.get(name)
            if isinstance(block, dict):
                return block
    return None


def _resets(block: dict, now: float | None = None) -> float | None:
    """When the window resets, as a unix timestamp. Codex sometimes answers with
    seconds-from-now instead of a date, which only makes sense against a clock."""
    for name in ("reset_at", "resets_at", "reset_time"):
        if name in block:
            return _when(block[name])
    seconds = block.get("resets_in_seconds", block.get("reset_after_seconds"))
    if isinstance(seconds, (int, float)):
        return (now or time.time()) + float(seconds)
    return None


def summarize(raw: dict, now: float | None = None) -> dict:
    """The two windows we show, as {"used": 0..100, "resets_at": ts | None}.
    Windows the response lacks are left out rather than shown as zero."""
    out = {}
    for key, ours in WINDOWS.items():
        block = _block(raw, key)
        if not isinstance(block, dict):
            continue
        used = block.get("used_percent", block.get("utilization", block.get("used")))
        if not isinstance(used, (int, float)) or isinstance(used, bool):
            continue
        out[ours] = {"used": int(round(max(0.0, min(100.0, float(used))))), "resets_at": _resets(block, now)}
    return out


def refresh(now: float | None = None) -> dict | None:
    """One poll: read the login, ask, remember. Returns the new summary (None = nothing usable)."""
    global _latest, _detail
    creds = credentials()
    if creds is None:
        with _lock:
            _detail = "no Codex login to read (sign in with Codex)"
        return None
    try:
        summary = summarize(fetch(creds["access_token"], creds["account_id"]), now)
        note = "usage read with Codex's login"
    except urllib.error.HTTPError as e:
        summary, note = None, f"usage endpoint answered {e.code}"
    except (OSError, ValueError) as e:
        summary, note = None, f"usage unavailable: {type(e).__name__}"
    with _lock:
        if summary is not None:
            _latest = summary
        _detail = note
    return summary
