"""Runtime paths a green build does not prove: HID recovery, Gemini identity,
Antigravity input state, quota freshness."""

import json
import os
import sqlite3
import threading
import time

import pytest

from lumen.devices import asus_aura
from lumen.integrations import agent_sessions as ag
from lumen.integrations import gemini
from lumen.integrations import google_usage as gu


class FakeHid:
    def __init__(self, fail_writes: int):
        self.fail_writes, self.frames, self.closed = fail_writes, 0, False

    def send_feature_report(self, data):
        if data[:2] == bytes([0x5D, 0xBC]) and data[2:5] == bytes([0x00, 0x01, 0x04]):
            if self.fail_writes:
                self.fail_writes -= 1
                raise OSError("device reset")
            self.frames += 1

    def close(self):
        self.closed = True


def _flush_with_timeout(c):
    errors = []
    t = threading.Thread(target=lambda: _catch(c.flush, errors), daemon=True)
    t.start()
    t.join(2.0)
    assert not t.is_alive(), "flush deadlocked"
    return errors


def _catch(fn, errors):
    try:
        fn()
    except Exception as e:
        errors.append(e)


def test_asus_write_failure_reopens_and_resends():
    handles = [FakeHid(fail_writes=1), FakeHid(fail_writes=0)]
    c = asus_aura.AuraController(b"path", 0x19B6)
    c._open = lambda: handles.pop(0) if handles else pytest.fail("opened too often")
    first = c._open()
    c._dev = first
    assert _flush_with_timeout(c) == []
    assert first.closed and c._dev.frames == 1


def test_asus_open_failure_is_reported_not_swallowed():
    c = asus_aura.AuraController(b"path", 0x19B6)

    def boom():
        raise OSError("no device")
    c._open = boom
    errors = _flush_with_timeout(c)
    assert errors and isinstance(errors[0], OSError) and c._dev is None


def test_gemini_hooks_keep_gemini_identity(tmp_path):
    payload = {"hook_event_name": "BeforeAgent", "session_id": "g1", "cwd": str(tmp_path),
               "transcript_path": str(tmp_path / ".gemini" / "tmp" / "chat.json")}
    assert ag.apply_hook(payload, state_dir=tmp_path) == ag.RUNNING
    record = json.loads((tmp_path / "g1.json").read_text())
    assert record["agent"] == "gemini" and record["status"] == ag.RUNNING
    assert ag.agent_of({"hook_event_name": "UserPromptSubmit"}) == "claude"


@pytest.mark.parametrize("db_status,expected", [(1, ag.INPUT), (2, ag.RUNNING), (3, ag.DONE)])
def test_antigravity_status_survives_reconciliation(tmp_path, db_status, expected):
    conv = tmp_path / "antigravity" / "conversations"
    conv.mkdir(parents=True)
    with sqlite3.connect(conv / "c1.db") as conn:
        conn.execute("CREATE TABLE steps (idx INT, step_type INT, status INT)")
        conn.execute("INSERT INTO steps VALUES (1, 0, ?)", (db_status,))
    conn.close()
    meta = {}
    truth = gemini.antigravity_states(home=tmp_path, metadata=meta)
    assert ag.session_statuses({}, truth, meta, meta) == {"c1": expected}


def _usage_line(tokens: int, pad: str = "") -> str:
    return json.dumps({"id": pad, "message": {"usage": {"input_tokens": tokens}}}) + "\n"


def test_transcript_replaced_at_same_path_is_recounted(tmp_path):
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(_usage_line(10, "a") + _usage_line(5, "a"))
    record = {}
    ag._accumulate_tokens(transcript, record)
    assert record["tokens"]["in"] == 15
    transcript.write_text(_usage_line(7, "b") + _usage_line(1, "b") + _usage_line(1, "b"))  # larger, different file
    ag._accumulate_tokens(transcript, record)
    assert record["tokens"]["in"] == 9
    with transcript.open("a") as f:  # an ordinary append still counts only the new bytes
        f.write(_usage_line(4, "b"))
    ag._accumulate_tokens(transcript, record)
    assert record["tokens"]["in"] == 13


def test_codex_rollout_replaced_at_same_path_is_rescanned(tmp_path):
    from lumen.integrations import codex
    rollout = tmp_path / "rollout.jsonl"
    event = lambda kind, pad: json.dumps({"type": "event_msg", "payload": {"type": kind, "pad": pad}}) + "\n"
    opened = next(k for k, v in codex.TURN_OPEN_EVENTS.items() if v)
    closed = next(k for k, v in codex.TURN_OPEN_EVENTS.items() if not v)
    rollout.write_text(event(opened, "aaaa"))
    assert codex.turn_open(rollout) is True
    rollout.write_text(event(closed, "bbbbbbbbbbbbbbbb"))  # same path, larger, turn closed
    assert codex.turn_open(rollout) is False


def test_gemini_hooks_use_millisecond_timeouts(tmp_path, monkeypatch):
    integ = gemini.GeminiIntegration()
    monkeypatch.setattr(integ, "hooks_file", tmp_path / "settings.json")
    integ.connect()
    hooks = json.loads((tmp_path / "settings.json").read_text())["hooks"]
    entry = hooks["BeforeAgent"][0]["hooks"][0]
    assert entry["timeout"] == 5000 and "async" not in entry


def test_unchanged_usage_file_goes_stale(tmp_path, monkeypatch):
    usage = tmp_path / "usage.json"
    usage.write_text(json.dumps({"daily": {"used": 40}}))
    old = time.time() - gu.STALE_S - 60
    os.utime(usage, (old, old))
    monkeypatch.setattr(gu, "usage_path", lambda: usage)
    monkeypatch.setattr(gu, "credentials", lambda: {"access_token": "t"})
    monkeypatch.setattr(gu, "_latest", None)
    monkeypatch.setattr(gu, "_latest_at", 0.0)
    assert gu.refresh() is None and gu.refresh() is None  # rereading does not renew it

    usage.write_text(json.dumps({"daily": {"used": 55}}))  # a genuinely newer observation
    assert gu.refresh()["daily"]["used"] == 55

    usage.write_text("{not json")
    assert gu.refresh()["daily"]["used"] == 55  # last good value, still within its window
