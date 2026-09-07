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

from lumen.integrations.claude_usage import STALE_S, _when

USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
AUTH_FILE = Path.home() / ".codex" / "auth.json"
# response key -> the window we assume it is when the response does not say how
# long the window actually is. Codex names its windows by rank, not by length,
# and the rank means different things on different plans: on a Pro Lite account
# `primary_window` is the *weekly* limit (limit_window_seconds 604800) and
# `secondary_window` is null, so trusting the rank labelled a 7-day limit that
# resets tomorrow as "5h" — and left it reading 100% long after the user had
# closed Codex, with no five-hour reset in sight to explain it.
WINDOWS = {"primary": "five_hour", "secondary": "seven_day"}
# A window is the short one or the long one, by its own stated length. Codex's
# short window is 5h (18000s) and its long one 7 days (604800s); the split sits
# far from both, so a plan with, say, a 24h window still lands somewhere sane.
LONG_WINDOW_S = 24 * 3600
POLL_S = 300

_lock = threading.Lock()
_latest: dict | None = None
_latest_at = 0.0
_detail = "not checked yet"


def latest(now: float | None = None) -> dict | None:
    """The last good reading, or None once it is older than STALE_S — the same
    staleness rule as Claude's, for the same reason: an expired Codex login
    keeps failing, and the meter should go quiet rather than keep showing what
    the numbers were when it last worked."""
    with _lock:
        if not _latest or (now or time.time()) - _latest_at > STALE_S:
            return None
        return dict(_latest)


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


def window_name(block: dict, fallback: str) -> str:
    """Which of our two windows this block is, by the length it declares.

    The rank in the key ("primary"/"secondary") is not the length: a Pro Lite
    account reports its 7-day limit as the primary window and no secondary one.
    `fallback` is the rank's guess, used only when the block does not say."""
    seconds = block.get("limit_window_seconds", block.get("window_seconds"))
    if isinstance(seconds, (int, float)) and not isinstance(seconds, bool) and seconds > 0:
        return "seven_day" if float(seconds) >= LONG_WINDOW_S else "five_hour"
    return fallback


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
        name = window_name(block, ours)
        # Two blocks can name the same window only if the response is odd; the
        # one that says its own length wins over the one that fell back.
        if name in out and name != ours:
            continue
        out[name] = {"used": int(round(max(0.0, min(100.0, float(used))))), "resets_at": _resets(block, now)}
    return out


def refresh(now: float | None = None) -> dict | None:
    """One poll: read the login, ask, remember. Returns the new summary (None = nothing usable)."""
    global _latest, _latest_at, _detail
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
            _latest_at = time.time() if now is None else now
        _detail = note
    return summary
