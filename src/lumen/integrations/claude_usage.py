"""Claude subscription usage — the 5-hour and 7-day limits Claude Code's own
`/usage` shows — read with the login Claude Code already holds.

The access token sits in the macOS login keychain ("Claude Code-credentials")
or in ~/.claude/.credentials.json elsewhere, against the same endpoint Claude
Code queries. Claude Code only rotates that token while the CLI itself runs;
the desktop app keeps its own login and leaves the file to age, so on a machine
that only uses the app the token is expired for good. When it is, and the
refresh token is still valid, Lumen refreshes it the way the CLI would and
writes the new pair back to the same file, so the CLI keeps working too. The
keychain is left read-only: an expired token there waits for the CLI.

    latest() -> {"five_hour": {"used": 23, "resets_at": 1788...}, "seven_day": {...},
                 "seven_day_opus": {...}, ...} or None
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
TOKEN_URL = "https://console.anthropic.com/v1/oauth/token"
CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"  # Claude Code's own OAuth client
KEYCHAIN_SERVICE = "Claude Code-credentials"
CREDENTIALS_FILE = Path.home() / ".claude" / ".credentials.json"
# response key -> ours. The endpoint names windows two ways: as top-level
# blocks (five_hour, seven_day, seven_day_opus, ...) and as a "limits" array of
# {kind, percent, resets_at} (session, weekly_all, weekly_opus, ...). Both are
# read; the named blocks win when they disagree.
WINDOWS = {"five_hour": "five_hour", "seven_day": "seven_day",
           "seven_day_opus": "seven_day_opus", "seven_day_sonnet": "seven_day_sonnet"}
KINDS = {"session": "five_hour", "weekly_all": "seven_day"}  # "limits" kind -> ours
LABELS = {"five_hour": "Session · 5 hours", "seven_day": "Week · 7 days",
          "seven_day_opus": "Week · Opus", "seven_day_sonnet": "Week · Sonnet"}
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
    """Each percentage as a plain int beside the nested blocks, so a rule can
    say "five_hour_used > 80" without reaching into a dict."""
    return {f"{key}_used": block["used"] for key, block in summary.items()
            if isinstance(block, dict) and isinstance(block.get("used"), int)}


def label(key: str) -> str:
    """What to call a window on a meter: the known ones by name, the rest tidied."""
    return LABELS.get(key) or "Week · " + key.removeprefix("seven_day_").replace("_", " ").capitalize()


def ordered(summary: dict) -> list[str]:
    """Meter order: session, all-models week, then the per-model weeks."""
    rank = {"five_hour": 0, "seven_day": 1}
    return sorted((k for k in summary if isinstance(summary[k], dict)), key=lambda k: (rank.get(k, 2), k))


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


def _read_store() -> dict:
    """The whole credential store (keychain item or file) as a dict, {} if unreadable."""
    try:
        if sys.platform == "darwin":
            out = subprocess.run(["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
                                 capture_output=True, text=True, timeout=5)
            text = out.stdout.strip() if out.returncode == 0 else ""
        else:
            text = CREDENTIALS_FILE.read_text(encoding="utf-8")
        data = json.loads(text) if text else {}
    except (OSError, ValueError, subprocess.SubprocessError):
        return {}
    return data if isinstance(data, dict) else {}


def _ms_past(value, now: float) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value / 1000 < now


def credentials(now: float | None = None) -> dict | None:
    """The claudeAiOauth block with a live access token, or None if there is no
    usable login (missing, unreadable, expired and not refreshable)."""
    now = now or time.time()
    data = _read_store()
    oauth = data.get("claudeAiOauth")
    if not isinstance(oauth, dict) or not oauth.get("accessToken"):
        return None
    if not _ms_past(oauth.get("expiresAt"), now):
        return oauth
    return renew(data, now)


def renew(data: dict, now: float | None = None) -> dict | None:
    """Trade the refresh token for a new pair and write it back where Claude Code
    reads it (the file; the keychain is left alone). None when there is no
    refresh token, it has expired too, or the server says no."""
    now = now or time.time()
    oauth = data.get("claudeAiOauth") if isinstance(data, dict) else None
    if sys.platform == "darwin" or not isinstance(oauth, dict) or not oauth.get("refreshToken") \
            or _ms_past(oauth.get("refreshTokenExpiresAt"), now):
        return None
    body = json.dumps({"grant_type": "refresh_token", "refresh_token": oauth["refreshToken"],
                       "client_id": CLIENT_ID}).encode("utf-8")
    req = urllib.request.Request(TOKEN_URL, data=body, headers={
        "Content-Type": "application/json", "Accept": "application/json", "User-Agent": "lumen"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            fresh = json.loads(r.read().decode("utf-8", "replace"))
    except (OSError, ValueError):
        return None
    if not isinstance(fresh, dict) or not fresh.get("access_token"):
        return None
    new = dict(oauth, accessToken=fresh["access_token"],
               expiresAt=int((now + float(fresh.get("expires_in") or 3600)) * 1000))
    if fresh.get("refresh_token"):
        new["refreshToken"] = fresh["refresh_token"]
    try:
        tmp = CREDENTIALS_FILE.with_suffix(".json.lumen")
        tmp.write_text(json.dumps(dict(data, claudeAiOauth=new)), encoding="utf-8")
        tmp.replace(CREDENTIALS_FILE)
    except OSError:
        pass  # still good for this poll; the file keeps the old pair
    return new


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


def _pct(used) -> int | None:
    if not isinstance(used, (int, float)) or isinstance(used, bool):
        return None
    return int(round(max(0.0, min(100.0, float(used)))))


def summarize(raw: dict) -> dict:
    """The windows we show, as {"used": 0..100, "resets_at": ts | None}. Windows the
    response lacks are left out rather than shown as zero."""
    out = {}
    limits = raw.get("limits")
    for item in limits if isinstance(limits, list) else []:
        if not isinstance(item, dict) or not isinstance(item.get("kind"), str):
            continue
        kind = item["kind"]
        # a scoped week names its model in `scope` ("weekly_scoped" + Fable ->
        # seven_day_fable); the unscoped kinds carry the name in the kind itself
        scope = item.get("scope") if isinstance(item.get("scope"), dict) else {}
        model = scope.get("model") if isinstance(scope.get("model"), dict) else {}
        name = model.get("display_name") if isinstance(model.get("display_name"), str) else ""
        tail = "".join(c if c.isalnum() else "_" for c in name.lower()).strip("_") or kind.removeprefix("weekly_")
        ours = KINDS.get(kind) or ("seven_day_" + tail if kind.startswith("weekly_") else None)
        used = _pct(item.get("percent", item.get("utilization")))
        if ours and used is not None:
            out[ours] = {"used": used, "resets_at": _when(item.get("resets_at"))}
    for key, ours in WINDOWS.items():
        block = raw.get(key)
        if not isinstance(block, dict):
            continue
        used = _pct(block.get("utilization", block.get("used")))
        if used is None:
            continue
        out[ours] = {"used": used, "resets_at": _when(block.get("resets_at"))}
    return out


def refresh(now: float | None = None) -> dict | None:
    """One poll: read the login, ask, remember. Returns the new summary (None = nothing usable)."""
    global _latest, _latest_at, _detail
    creds = credentials(now)
    if creds is None:
        with _lock:
            _detail = "no Claude Code login to read (run `claude` once to sign in)"
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
