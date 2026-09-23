"""Google & Gemini quota usage, read from the file Antigravity keeps locally
(~/.gemini/antigravity/usage.json). There is no remote endpoint.

Matches the same output shape as claude_usage and codex_usage:
    latest() -> {"five_hour": {"used": 15, "resets_at": 1788...},
                 "daily": {"used": 42, "resets_at": 1788...},
                 "gemini_flash": {"used": 20}, "gemini_pro": {"used": 55}} or None
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from lumen.integrations.claude_usage import STALE_S, _when

WINDOWS = {
    "five_hour": "five_hour",
    "daily": "daily",
    "seven_day": "seven_day",
    "gemini_flash": "gemini_flash",
    "gemini_pro": "gemini_pro",
    "gemini_flash_lite": "gemini_flash_lite",
}

LABELS = {
    "five_hour": "Session · 5 hours",
    "daily": "Daily · 24 hours",
    "seven_day": "Week · 7 days",
    "gemini_flash": "Gemini Flash",
    "gemini_pro": "Gemini Pro",
    "gemini_flash_lite": "Gemini Flash-Lite",
}

POLL_S = 300
SAMPLES = 12

_lock = threading.Lock()
_latest: dict | None = None
_latest_at = 0.0
_detail = "not checked yet"
_samples: list[tuple[float, float]] = []


def latest(now: float | None = None) -> dict | None:
    with _lock:
        if not _latest or (now or time.time()) - _latest_at > STALE_S:
            return None
        out = dict(_latest)
    eta = eta_full()
    if eta is not None:
        out["eta_full_s"] = eta
    return out


def detail() -> str:
    with _lock:
        return _detail


def flat(summary: dict) -> dict:
    """Each percentage as a plain int beside the nested blocks, so a rule can
    say 'daily_used > 80' without reaching into a dict."""
    return {f"{key}_used": block["used"] for key, block in summary.items()
            if isinstance(block, dict) and isinstance(block.get("used"), (int, float))}


def label(key: str) -> str:
    return LABELS.get(key) or "Gemini · " + key.replace("gemini_", "").replace("_", " ").capitalize()


def ordered(summary: dict) -> list[str]:
    rank = {"five_hour": 0, "daily": 1, "seven_day": 2, "gemini_flash": 3, "gemini_pro": 4, "gemini_flash_lite": 5}
    return sorted((k for k in summary if isinstance(summary[k], dict)), key=lambda k: (rank.get(k, 10), k))


def eta_full(now: float | None = None) -> float | None:
    with _lock:
        samples = list(_samples)
    if len(samples) < 2:
        return None
    (t0, u0), (t1, u1) = samples[0], samples[-1]
    rate = (u1 - u0) / (t1 - t0) if t1 > t0 else 0.0
    if rate <= 0:
        return None
    return max(0.0, (100 - u1) / rate)


def usage_path() -> Path:
    return Path.home() / ".gemini" / "antigravity" / "usage.json"


def read_local_antigravity_usage() -> dict | None:
    """The file's contents, with `_observed_at` = its mtime: rereading an old
    file must not make its numbers fresh again."""
    usage_file = usage_path()
    try:
        data = json.loads(usage_file.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data["_observed_at"] = usage_file.stat().st_mtime
            return data
    except (OSError, ValueError):
        return None
    return None


def _pct(used) -> float | None:
    if not isinstance(used, (int, float)) or isinstance(used, bool):
        return None
    return round(max(0.0, min(100.0, float(used))), 2)


def summarize(raw: dict, now: float | None = None) -> dict:
    if not isinstance(raw, dict):
        return {}
    out = {}
    for k, v in raw.items():
        if isinstance(v, dict):
            used = _pct(v.get("utilization", v.get("used_percent", v.get("used"))))
            if used is not None:
                out[k] = {"used": used, "resets_at": _when(v.get("resets_at", v.get("reset_at")))}
        elif isinstance(v, (int, float)) and not isinstance(v, bool) and k in WINDOWS:
            used = _pct(v)
            if used is not None:
                out[k] = {"used": used, "resets_at": None}
    return out


def refresh(now: float | None = None) -> dict | None:
    """Antigravity writes its quota to a local file; that file is the only
    source. (Earlier builds also read a secret from the credential store and
    API keys from the environment, then used neither.)"""
    global _detail, _latest, _latest_at
    now_ts = now if now else time.time()
    data = read_local_antigravity_usage()
    summary = summarize(data, now_ts) if data else {}
    if data and summary:
        observed = data.get("_observed_at")
        observed = min(now_ts, float(observed)) if isinstance(observed, (int, float)) else now_ts
        with _lock:
            if observed > _latest_at or summary != _latest:
                _latest, _latest_at = summary, observed
                if "five_hour" in summary:
                    _record_sample(observed, summary["five_hour"]["used"])
            _detail = "usage read from Antigravity"
        return latest(now_ts)
    with _lock:
        _detail = "Antigravity quota data unavailable" if data else f"no quota file at {usage_path()}"
    return latest(now_ts)


def _record_sample(ts: float, used: float) -> None:
    _samples.append((ts, used))
    if len(_samples) > SAMPLES:
        _samples[:] = _samples[-SAMPLES:]
