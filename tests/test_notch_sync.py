import json

import pytest

from lumen.core import effects
from lumen.core.effects import EffectPlayer
from lumen.core.rules import Action
from lumen.devices.notch import Notch
from lumen.integrations import agent_sessions, claude_usage, codex_usage


@pytest.mark.parametrize("per_zone", [False, True])
def test_notch_refreshes_metadata_without_a_color_change(monkeypatch, per_zone):
    now = 100.0
    monkeypatch.setattr(effects.time, "monotonic", lambda: now)
    sessions = [{"id": "old", "agent": "codex", "status": "done", "slot": 0}]
    usage = {"seven_day_used": 2}
    monkeypatch.setattr(agent_sessions, "all_sessions", lambda: sessions)
    monkeypatch.setattr(claude_usage, "latest", lambda: {})
    monkeypatch.setattr(codex_usage, "latest", lambda: usage)
    notch = Notch()
    sent = []
    monkeypatch.setattr(notch, "_send", sent.append)
    player = EffectPlayer()
    player.set_devices([notch])
    player.run(Action(device="notch", effect="sessions", per_zone=per_zone))
    initial_colors = sent[-1].split(" | ")[0]

    sessions = [{"id": "new", "agent": "codex", "status": "running", "slot": 0}]
    usage = {"seven_day_used": 3}
    notch.position = "bottom"
    now += 0.49
    player._tick(now)
    assert len(sent) == 1
    now += 0.02
    player._tick(now)

    assert len(sent) == 2
    colors, raw = sent[-1].split(" | ")
    assert colors == initial_colors
    payload = json.loads(raw)
    assert payload["sessions"] == sessions
    assert payload["usage"]["codex"] == usage
    assert payload["options"]["position"] == "bottom"
