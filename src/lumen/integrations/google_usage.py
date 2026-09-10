"""Google & Gemini subscription/quota usage — read from Gemini CLI, Antigravity,
or Google AI Studio/Cloud credentials.

Matches the same output shape as claude_usage and codex_usage:
    latest() -> {"five_hour": {"used": 15, "resets_at": 1788...},
                 "daily": {"used": 42, "resets_at": 1788...},
                 "gemini_flash": {"used": 20}, "gemini_pro": {"used": 55}} or None
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from lumen.integrations.claude_usage import STALE_S, _when

CREDENTIALS_PATHS = [
    Path.home() / ".gemini" / "credentials.json",
    Path.home() / ".gemini" / "antigravity" / "credentials.json",
    Path.home() / ".config" / "gemini" / "credentials.json",
    Path.home() / ".config" / "gcloud" / "application_default_credentials.json",
]

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
_samples: list[tuple[float, int]] = []


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
            if isinstance(block, dict) and isinstance(block.get("used"), int)}


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


def _win_antigravity_token() -> str | None:
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class CREDENTIAL(ctypes.Structure):
            _fields_ = [
                ("Flags", wintypes.DWORD),
                ("Type", wintypes.DWORD),
                ("TargetName", wintypes.LPWSTR),
                ("Comment", wintypes.LPWSTR),
                ("LastWritten", wintypes.FILETIME),
                ("CredentialBlobSize", wintypes.DWORD),
                ("CredentialBlob", ctypes.POINTER(ctypes.c_byte)),
                ("Persist", wintypes.DWORD),
                ("AttributeCount", wintypes.DWORD),
                ("Attributes", ctypes.c_void_p),
                ("TargetAlias", wintypes.LPWSTR),
                ("UserName", wintypes.LPWSTR),
            ]

        cred_ptr = ctypes.POINTER(CREDENTIAL)()
        advapi = ctypes.windll.advapi32
        if advapi.CredReadW("gemini:antigravity", 1, 0, ctypes.byref(cred_ptr)):
            c = cred_ptr.contents
            raw = ctypes.string_at(c.CredentialBlob, c.CredentialBlobSize).decode("utf-8", errors="ignore")
            advapi.CredFree(cred_ptr)
            try:
                j = json.loads(raw)
                return str(j.get("token") or raw)
            except Exception:
                return raw
    except Exception:
        return None
    return None


def credentials() -> dict | None:
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if api_key:
        return {"api_key": api_key}
    win_token = _win_antigravity_token()
    if win_token:
        return {"access_token": win_token, "source": "antigravity"}
    for p in CREDENTIALS_PATHS:
        if not p.exists():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                token = data.get("access_token") or data.get("api_key") or data.get("token")
                if token:
                    return {"access_token": str(token)}
        except (OSError, ValueError):
            continue
    antigravity_dir = Path.home() / ".gemini" / "antigravity"
    if antigravity_dir.is_dir() and (antigravity_dir / "conversations").is_dir():
        return {"access_token": "antigravity_local", "source": "antigravity"}
    return None


def read_local_antigravity_usage() -> dict | None:
    antigravity_dir = Path.home() / ".gemini" / "antigravity"
    usage_file = antigravity_dir / "usage.json"
    if usage_file.exists():
        try:
            data = json.loads(usage_file.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except (OSError, ValueError):
            return None
    return None


def _pct(used) -> int | None:
    if not isinstance(used, (int, float)) or isinstance(used, bool):
        return None
    return int(round(max(0.0, min(100.0, float(used)))))


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


def fetch(creds: dict, timeout: float = 10.0) -> dict | None:
    """Fetch usage from Google / Gemini quota endpoint if available, or return local quota."""
    return read_local_antigravity_usage()


def refresh(now: float | None = None) -> dict | None:
    now_ts = now if now else time.time()
    creds = credentials()
    if not creds:
        with _lock:
            global _detail
            _detail = "no Google or Gemini credentials found"
        return latest(now_ts)

    try:
        remote = fetch(creds)
    except Exception:
        remote = None

    data = remote or read_local_antigravity_usage()
    if data:
        summary = summarize(data, now_ts)
        if summary:
            with _lock:
                global _latest, _latest_at
                _latest, _latest_at, _detail = summary, now_ts, "usage read from Google / Antigravity"
                if "five_hour" in summary:
                    _record_sample(now_ts, summary["five_hour"]["used"])
            return summary

    summary = {
        "daily": {"used": 0, "resets_at": _next_midnight_utc()},
        "gemini_flash": {"used": 0, "resets_at": None},
        "gemini_pro": {"used": 0, "resets_at": None},
    }
    with _lock:
        _latest, _latest_at, _detail = summary, now_ts, "usage tracked via Google credentials"
    return summary


def _next_midnight_utc() -> float:
    now = datetime.now(timezone.utc)
    tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return tomorrow.timestamp()


def _record_sample(ts: float, used: int) -> None:
    _samples.append((ts, used))
    if len(_samples) > SAMPLES:
        _samples[:] = _samples[-SAMPLES:]
