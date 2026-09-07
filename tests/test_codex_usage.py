"""Codex plan limits, read with Codex's own login. All faked — no network, no ~/.codex."""

import json
import urllib.error

from lumen.integrations import codex_usage as cx


def test_summary_tolerates_every_shape_the_endpoint_answers_with():
    nested = {"rate_limit": {"primary_window": {"used_percent": 23, "reset_at": "2026-09-07T12:00:00Z"},
                             "secondary_window": {"used_percent": 140, "resets_at": 1788700000}}}
    out = cx.summarize(nested)
    assert out["five_hour"] == {"used": 23, "resets_at": 1788782400.0}
    assert out["seven_day"] == {"used": 100, "resets_at": 1788700000.0}      # clamped
    # top level, with and without the _window suffix
    assert cx.summarize({"primary_window": {"used_percent": 5}}) == {"five_hour": {"used": 5, "resets_at": None}}
    assert cx.summarize({"primary": {"used": 5.6}, "secondary": {"utilization": 0}}) == {
        "five_hour": {"used": 6, "resets_at": None}, "seven_day": {"used": 0, "resets_at": None}}
    # a reset given as seconds from now needs a clock
    assert cx.summarize({"primary": {"used_percent": 1, "resets_in_seconds": 600}}, now=1000)["five_hour"][
        "resets_at"] == 1600.0
    # nothing usable is left out rather than shown as zero
    assert cx.summarize({}) == {}
    assert cx.summarize({"primary": {"used_percent": "n/a"}, "secondary": "x"}) == {}


def test_credentials_come_from_codex_auth_file_only(tmp_path, monkeypatch):
    f = tmp_path / "auth.json"
    monkeypatch.setattr(cx, "AUTH_FILE", f)
    assert cx.credentials() is None                                            # no file
    f.write_text("{not json")
    assert cx.credentials() is None
    f.write_text(json.dumps({"tokens": {"access_token": "", "account_id": "acc"}}))
    assert cx.credentials() is None
    f.write_text(json.dumps({"tokens": {"access_token": "tok", "account_id": "acc"}, "OPENAI_API_KEY": None}))
    assert cx.credentials() == {"access_token": "tok", "account_id": "acc"}


def test_refresh_remembers_the_last_good_answer(monkeypatch):
    monkeypatch.setattr(cx, "_latest", None)
    monkeypatch.setattr(cx, "credentials", lambda: {"access_token": "tok", "account_id": "acc"})
    seen = {}

    def fake_fetch(token, account_id="", timeout=10):
        seen.update(token=token, account_id=account_id)
        return {"rate_limit": {"primary_window": {"used_percent": 40}}}

    monkeypatch.setattr(cx, "fetch", fake_fetch)
    assert cx.refresh() == {"five_hour": {"used": 40, "resets_at": None}}
    assert seen == {"token": "tok", "account_id": "acc"}
    assert cx.latest() == {"five_hour": {"used": 40, "resets_at": None}}

    def boom(token, account_id="", timeout=10):
        raise urllib.error.HTTPError(cx.USAGE_URL, 401, "expired", {}, None)

    monkeypatch.setattr(cx, "fetch", boom)
    assert cx.refresh() is None
    assert cx.latest() == {"five_hour": {"used": 40, "resets_at": None}} and "401" in cx.detail()
    monkeypatch.setattr(cx, "credentials", lambda: None)
    assert cx.refresh() is None and "sign in" in cx.detail()
