"""Claude subscription limits, read with Claude Code's own login. All faked — no network, no keychain."""

import json
import urllib.error

from lumen.integrations import claude_usage as cu


def test_summary_keeps_only_the_windows_we_show():
    raw = {"five_hour": {"utilization": 23.4, "resets_at": "2026-09-07T12:00:00Z"},
           "seven_day": {"utilization": 140, "resets_at": 1788700000},
           "seven_day_opus": {"utilization": 5}, "noise": 1}
    out = cu.summarize(raw)
    assert out["five_hour"]["used"] == 23 and out["five_hour"]["resets_at"] == 1788782400.0
    assert out["seven_day"] == {"used": 100, "resets_at": 1788700000.0}      # clamped, numeric timestamp accepted
    assert "seven_day_opus" not in out
    assert cu.summarize({"five_hour": {"utilization": "n/a"}, "seven_day": "x"}) == {}
    assert cu.summarize({"five_hour": {"used": 0.5, "resets_at": "garbage"}}) == {"five_hour": {"used": 0, "resets_at": None}}


def test_credentials_are_read_only_and_expired_ones_are_ignored(tmp_path, monkeypatch):
    monkeypatch.setattr(cu.sys, "platform", "win32")
    f = tmp_path / ".credentials.json"
    monkeypatch.setattr(cu, "CREDENTIALS_FILE", f)
    assert cu.credentials() is None                                            # no file
    f.write_text("{not json")
    assert cu.credentials() is None
    f.write_text(json.dumps({"claudeAiOauth": {"accessToken": "tok", "expiresAt": 2_000_000}}))
    assert cu.credentials(now=1_000)["accessToken"] == "tok"                  # expiresAt is in ms
    assert cu.credentials(now=3_000) is None                                   # expired: Lumen never refreshes it
    f.write_text(json.dumps({"claudeAiOauth": {"accessToken": ""}}))
    assert cu.credentials() is None


def test_refresh_remembers_the_last_good_answer(monkeypatch):
    monkeypatch.setattr(cu, "_latest", None)
    monkeypatch.setattr(cu, "credentials", lambda now=None: {"accessToken": "tok"})
    monkeypatch.setattr(cu, "fetch", lambda token, timeout=10: {"five_hour": {"utilization": 40, "resets_at": None}})
    assert cu.refresh() == {"five_hour": {"used": 40, "resets_at": None}}
    assert cu.latest() == {"five_hour": {"used": 40, "resets_at": None}}

    def boom(token, timeout=10):
        raise urllib.error.HTTPError(cu.USAGE_URL, 401, "expired", {}, None)
    monkeypatch.setattr(cu, "fetch", boom)
    assert cu.refresh() is None
    assert cu.latest() == {"five_hour": {"used": 40, "resets_at": None}} and "401" in cu.detail()
    monkeypatch.setattr(cu, "credentials", lambda now=None: None)
    assert cu.refresh() is None and "sign in" in cu.detail()


def test_a_stale_reading_is_not_passed_off_as_current(monkeypatch):
    """The login expires, every poll fails, and the last good numbers must stop
    being shown — a frozen "58%" reads as now, not as hours ago."""
    monkeypatch.setattr(cu, "_latest", None)
    monkeypatch.setattr(cu, "_samples", [])
    monkeypatch.setattr(cu, "credentials", lambda now=None: {"accessToken": "tok"})
    monkeypatch.setattr(cu, "fetch", lambda token, timeout=10: {"five_hour": {"utilization": 58}})
    cu.refresh(now=1_000)
    assert cu.latest(now=1_000 + cu.STALE_S)["five_hour"]["used"] == 58   # still inside the window
    assert cu.latest(now=1_000 + cu.STALE_S + 1) is None                  # past it: nothing rather than an old number
    monkeypatch.setattr(cu, "credentials", lambda now=None: None)         # login gone; _latest is left alone
    assert cu.refresh(now=1_000) is None
    assert cu.latest(now=1_000 + cu.STALE_S + 1) is None
    assert "sign in with Claude Code" in cu.detail()


def test_reset_countdown_reads_like_a_person_would_say_it():
    assert cu.resets_in(None) == ""
    assert cu.resets_in(100 + 35 * 60, now=100) == "35m"
    assert cu.resets_in(100 + 2 * 3600 + 5 * 60, now=100) == "2h 05m"
    assert cu.resets_in(100 + 3 * 86400 + 4 * 3600, now=100) == "3d 4h"
    assert cu.resets_in(50, now=100) == "0m"


def test_percentages_are_flattened_for_rules():
    summary = {"five_hour": {"used": 40, "resets_at": None}, "seven_day": {"used": 7, "resets_at": 1}}
    assert cu.flat(summary) == {"five_hour_used": 40, "seven_day_used": 7}
    assert cu.flat({"five_hour": "x", "seven_day": {"resets_at": 1}}) == {}


def test_burn_rate_needs_two_rising_samples(monkeypatch):
    monkeypatch.setattr(cu, "_samples", [])
    monkeypatch.setattr(cu, "_latest", None)
    monkeypatch.setattr(cu, "credentials", lambda now=None: {"accessToken": "tok"})
    used = [10, 20, 5] + [50] * 30
    monkeypatch.setattr(cu, "fetch", lambda token, timeout=10: {"five_hour": {"utilization": used.pop(0)}})
    cu.refresh(now=0)
    assert cu.eta_full() is None                      # one sample says nothing about a rate
    cu.refresh(now=100)                               # +10% in 100s -> 80% left = 800s
    assert cu.eta_full() == 800
    assert cu.latest(now=100)["eta_full_s"] == 800
    cu.refresh(now=200)                               # usage fell (the window reset): no ETA
    assert cu.eta_full() is None and "eta_full_s" not in cu.latest(now=200)
    # the window stays small no matter how long the daemon runs
    for i in range(30):
        cu.refresh(now=1000 + i)
    assert len(cu._samples) == cu.SAMPLES
