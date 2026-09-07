"""Claude subscription usage — the 5-hour and 7-day limits Claude Code's own
`/usage` shows — read with the login Claude Code already holds.

The access token sits in the macOS login keychain ("Claude Code-credentials")
or in ~/.claude/.credentials.json elsewhere. It is used read-only, against the
same endpoint Claude Code queries; Lumen never refreshes it (a rotated refresh
token would log Claude Code out), so an expired token simply means "no
numbers until Claude Code signs in again".

    latest() -> {"five_hour": {"used": 23, "resets_at": 1788...}, "seven_day": {...}} or None
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
KEYCHAIN_SERVICE = "Claude Code-credentials"
CREDENTIALS_FILE = Path.home() / ".claude" / ".credentials.json"
WINDOWS = {"five_hour": "five_hour", "seven_day": "seven_day"}  # response key -> ours
POLL_S = 300
# How old the last good reading may be before we stop showing it. Four missed
# polls: long enough that one flaky request or a laptop waking up does not blank
# the meters, short enough that a login which went stale (Lumen never refreshes
# the token, so an expired one stays expired) stops being reported as current
# within half an hour. A number nobody can tell is hours old is worse than none.
STALE_S = 4 * POLL_S

_lock = threading.Lock()
_latest: dict | None = None
_latest_at = 0.0
_detail = "not checked yet"
_samples: list[tuple[float, int]] = []  # (ts, five_hour used) — the burn rate window
SAMPLES = 12


def latest(now: float | None = None) -> dict | None:
    with _lock:
        if not _latest or (now or time.time()) - _latest_at > STALE_S:
            return None
        out = dict(_latest)
    eta = eta_full()
    if eta is not None:
        out["eta_full_s"] = eta
    return out


def flat(summary: dict) -> dict:
    """The two percentages as plain ints beside the nested blocks, so a rule can
    say "five_hour_used > 80" without reaching into a dict."""
    return {f"{key}_used": summary[key]["used"] for key in ("five_hour", "seven_day")
            if isinstance(summary.get(key), dict) and isinstance(summary[key].get("used"), int)}


def eta_full(now: float | None = None) -> float | None:
    """Seconds until the 5-hour window hits 100% at the rate it has been
    filling. None while we have too few samples, or when usage is flat or
    falling (the window reset, or you stopped working — either way, no ETA)."""
    with _lock:
        samples = list(_samples)
    if len(samples) < 2:
        return None
    (t0, u0), (t1, u1) = samples[0], samples[-1]
    rate = (u1 - u0) / (t1 - t0) if t1 > t0 else 0.0
    if rate <= 0:
        return None
    return max(0.0, (100 - u1) / rate)


def detail() -> str:
    with _lock:
        return _detail


def credentials(now: float | None = None) -> dict | None:
    """The claudeAiOauth block, or None if there is no usable login (missing, unreadable, expired)."""
    try:
        if sys.platform == "darwin":
            out = subprocess.run(["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
                                 capture_output=True, text=True, timeout=5)
            text = out.stdout.strip() if out.returncode == 0 else ""
        else:
            text = CREDENTIALS_FILE.read_text(encoding="utf-8")
        data = json.loads(text) if text else {}
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    oauth = data.get("claudeAiOauth") if isinstance(data, dict) else None
    if not isinstance(oauth, dict) or not oauth.get("accessToken"):
        return None
    expires = oauth.get("expiresAt")
    if isinstance(expires, (int, float)) and expires / 1000 < (now or time.time()):
        return None
    return oauth


def fetch(token: str, timeout: float = 10) -> dict:
    req = urllib.request.Request(USAGE_URL, headers={
        "Authorization": f"Bearer {token}", "anthropic-beta": "oauth-2025-04-20",
        "Accept": "application/json", "User-Agent": "lumen"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read().decode("utf-8", "replace"))
    return data if isinstance(data, dict) else {}


def _when(value) -> float | None:
    """resets_at as a unix timestamp: ISO-8601 text or a number, else None."""
    if isinstance(value, (int, float)):
        return float(value) if value < 1e12 else value / 1000
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def summarize(raw: dict) -> dict:
    """The two windows we show, as {"used": 0..100, "resets_at": ts | None}. Windows the
    response lacks are left out rather than shown as zero."""
    out = {}
    for key, ours in WINDOWS.items():
        block = raw.get(key)
        if not isinstance(block, dict):
            continue
        used = block.get("utilization", block.get("used"))
        if not isinstance(used, (int, float)):
            continue
        out[ours] = {"used": int(round(max(0.0, min(100.0, float(used))))), "resets_at": _when(block.get("resets_at"))}
    return out


def refresh(now: float | None = None) -> dict | None:
    """One poll: read the login, ask, remember. Returns the new summary (None = nothing usable)."""
    global _latest, _latest_at, _detail
    creds = credentials(now)
    if creds is None:
        with _lock:
            _detail = "no Claude Code login to read (sign in with Claude Code)"
        return None
    try:
        summary = summarize(fetch(creds["accessToken"]))
        note = "usage read with Claude Code's login"
    except urllib.error.HTTPError as e:
        summary, note = None, f"usage endpoint answered {e.code}"
    except (OSError, ValueError) as e:
        summary, note = None, f"usage unavailable: {type(e).__name__}"
    with _lock:
        if summary is not None:
            _latest = summary
            _latest_at = time.time() if now is None else now
            used = (summary.get("five_hour") or {}).get("used")
            if isinstance(used, int):
                _samples.append((time.time() if now is None else now, used))
                del _samples[:-SAMPLES]
        _detail = note
    return summary


def resets_in(ts: float | None, now: float | None = None) -> str:
    """"2h 10m", "35m", "3d 4h", or "" when unknown."""
    if ts is None:
        return ""
    left = max(0, int(ts - (now or time.time())))
    d, rem = divmod(left, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    if d:
        return f"{d}d {h}h"
    if h:
        return f"{h}h {m:02d}m"
    return f"{m}m"
