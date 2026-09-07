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


def test_reset_countdown_reads_like_a_person_would_say_it():
    assert cu.resets_in(None) == ""
    assert cu.resets_in(100 + 35 * 60, now=100) == "35m"
    assert cu.resets_in(100 + 2 * 3600 + 5 * 60, now=100) == "2h 05m"
    assert cu.resets_in(100 + 3 * 86400 + 4 * 3600, now=100) == "3d 4h"
    assert cu.resets_in(50, now=100) == "0m"
