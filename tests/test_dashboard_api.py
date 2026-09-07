"""Phase 2: live updates, session ordering, presets, dimmers and quiet hours."""

import time
import urllib.request

import pytest

from lumen.core import slots
from lumen.core.config import Config
from lumen.core.effects import EffectPlayer, within
from lumen.integrations import agent_sessions as ag


# --- live updates -------------------------------------------------------------
def test_revision_moves_on_every_change(server):
    call, engine, *_ = server
    before = call("GET", "/api/state")[1]["revision"]
    call("POST", "/api/events", {"type": "build.failed"})
    after = call("GET", "/api/state")[1]["revision"]
    assert after > before


def test_stream_pushes_a_revision_when_something_happens(server):
    call, engine, *_ = server
    req = urllib.request.Request(call.base + "/api/stream", headers={"Host": call.base.split("//")[1]})
    with urllib.request.urlopen(req, timeout=5) as stream:
        assert stream.headers["Content-Type"] == "text/event-stream"
        first = stream.readline()  # the revision as it stands
        assert first.startswith(b"data: ")
        stream.readline()
        call("POST", "/api/events", {"type": "build.failed"})
        pushed = stream.readline()
        assert pushed.startswith(b"data: ")
        assert int(pushed.split(b" ")[1]) > int(first.split(b" ")[1])


# --- agent tab order ----------------------------------------------------------
@pytest.fixture
def three_tabs(tmp_path, monkeypatch):
    monkeypatch.setenv("LUMEN_HOME", str(tmp_path))
    ag._sessions.clear()
    ag._snapshot.clear()
    records = {sid: {"started": 1_700_000_000 + i, "cwd": f"C:/p{i}"} for i, sid in enumerate(["a", "b", "c"])}
    ag._update_sessions("claude", {sid: ag.RUNNING for sid in records}, records)
    return records


def test_a_new_tab_opens_on_the_first_zone(three_tabs):
    """With more tabs than zones, the tab you just started is the one you want
    to see: it takes zone 0 and pushes the rest along."""
    assert [s["id"] for s in ag.all_sessions()] == ["c", "b", "a"]  # c started last
    ag._update_sessions("claude", {sid: ag.RUNNING for sid in [*three_tabs, "d"]},
                        {**three_tabs, "d": {"started": 1_700_000_009, "cwd": "C:/p3"}})
    assert [s["id"] for s in ag.all_sessions()] == ["d", "c", "b", "a"]


def test_set_slot_swaps_and_leaves_everyone_else_alone(three_tabs):
    ag.set_slot("c", 0)
    assert {s["id"]: s["slot"] for s in ag.all_sessions()} == {"c": 0, "b": 1, "a": 2}
    with pytest.raises(ValueError):
        ag.set_slot("c", 99)
    with pytest.raises(ValueError):
        ag.set_slot("nope", 1)


def test_set_order_lays_tabs_over_the_zones_they_hold(three_tabs):
    ag.set_order(["c", "a", "b"])
    assert [s["id"] for s in ag.all_sessions()] == ["c", "a", "b"]
    with pytest.raises(ValueError):
        ag.set_order(["a", "a"])


def test_a_pinned_zone_outlives_the_tab_and_the_daemon(three_tabs, tmp_path):
    ag.set_slot("a", 2)
    ag.set_label("a", "API refactor")
    ag._sessions.clear()          # every tab closed; daemon restarted
    ag._snapshot.clear()
    ag._update_sessions("claude", {"a": ag.RUNNING}, {"a": {"started": 9, "cwd": "C:/p0"}})
    restored = ag.all_sessions()[0]
    assert restored["slot"] == 2 and restored["label"] == "API refactor"


def test_a_new_tab_in_a_pinned_project_lands_on_that_zone(three_tabs):
    ag.set_slot("a", 3)           # pins both id:a and cwd:C:/p0
    ag._sessions.clear()
    ag._snapshot.clear()
    ag._update_sessions("claude", {"fresh": ag.RUNNING}, {"fresh": {"started": 1, "cwd": "C:/p0"}})
    assert ag.all_sessions()[0]["slot"] == 3


def test_slot_pins_are_forgotten_after_a_week(tmp_path):
    old = time.time() - slots.FORGET_AFTER_S - 1
    slots.save({"id:x": {"slot": 3, "ts": old}, "id:y": {"slot": 1, "ts": time.time()}}, tmp_path)
    assert set(slots.load(tmp_path)) == {"id:y"}


def test_session_endpoints(server, three_tabs):
    call, *_ = server
    assert call("PATCH", "/api/sessions/a", {"slot": 2})[0] == 200
    assert [s["id"] for s in ag.all_sessions()] == ["c", "b", "a"]
    assert call("PUT", "/api/sessions/order", ["a", "b", "c"])[0] == 200
    assert [s["id"] for s in ag.all_sessions()] == ["a", "b", "c"]
    assert call("PATCH", "/api/sessions/a", {"label": "API refactor"})[1]["session"]["label"] == "API refactor"
    assert call("PATCH", "/api/sessions/a", {})[0] == 400
    assert call("PATCH", "/api/sessions/../../etc", {"slot": 0})[0] in (400, 404)


def test_reordering_tabs_repaints_immediately(server, three_tabs):
    """No status changed, so the pollers see nothing: the reorder itself has to
    push the new layout, or the keyboard keeps the old one until a tab moves on."""
    call, engine, *_ = server
    seen = []
    engine.bus.subscribe(lambda e: seen.append(e) if e.type == "agents.sessions" else None)
    call("PUT", "/api/sessions/order", ["c", "b", "a"])
    assert [s["id"] for s in seen[-1].data["sessions"]] == ["c", "b", "a"]
    call("PATCH", "/api/sessions/a", {"slot": 0})
    assert [s["id"] for s in seen[-1].data["sessions"]] == ["a", "b", "c"]


# --- presets ------------------------------------------------------------------
def test_presets_crud(server):
    call, *_ = server
    actions = [{"device": "*", "effect": "flash", "color": [0, 255, 0]}]
    saved = call("POST", "/api/presets", {"name": "Success", "actions": actions})[1]
    assert saved["id"] and saved["name"] == "Success"
    assert call("GET", "/api/presets")[1] == [saved]
    assert call("POST", "/api/presets", {"name": "", "actions": actions})[0] == 400
    assert call("POST", "/api/presets", {"name": "Empty", "actions": []})[0] == 400
    assert call("DELETE", f"/api/presets/{saved['id']}")[1] == {"deleted": True}


# --- per-device dimmer --------------------------------------------------------
def test_device_dimmer_scales_what_reaches_the_hardware(server):
    call, engine, light, _ = server
    call("PATCH", "/api/devices/light", {"brightness": 0.5})
    call("POST", "/api/rules/preview", [{"device": "light", "effect": "set", "color": [200, 100, 0]}])
    assert light.colors[-1] == (100, 50, 0)
    assert call("PATCH", "/api/devices/light", {"brightness": "bright"})[0] == 400
    assert call("GET", "/api/state")[1]["devices"][0]["brightness"] == 0.5


def test_forgetting_a_device_needs_it_to_be_gone(server):
    call, engine, *_ = server
    call("PATCH", "/api/devices/light", {"name": "Desk lamp"})
    assert call("DELETE", "/api/devices/light")[0] == 400  # still connected
    engine.devices = [d for d in engine.devices if d.id != "light"]
    assert call("GET", "/api/state")[1]["forgotten_devices"] == ["light"]
    assert call("DELETE", "/api/devices/light")[1] == {"forgotten": True}
    assert call("GET", "/api/state")[1]["forgotten_devices"] == []


# --- quiet hours --------------------------------------------------------------
@pytest.mark.parametrize("now, expected", [
    ((23, 30), True), ((2, 0), True), ((6, 59), True), ((7, 0), False), ((12, 0), False), ((22, 59), False),
])
def test_quiet_window_wraps_midnight(now, expected):
    window = {"enabled": True, "from": "23:00", "to": "07:00"}
    assert within(window, time.struct_time((2026, 1, 1, now[0], now[1], 0, 0, 1, 0))) is expected


def test_quiet_window_off_and_degenerate():
    at_two = time.struct_time((2026, 1, 1, 2, 0, 0, 0, 1, 0))
    assert within({"enabled": False, "from": "23:00", "to": "07:00"}, at_two) is False
    assert within({"enabled": True, "from": "07:00", "to": "07:00"}, at_two) is False
    assert within({"enabled": True, "from": "oops", "to": "07:00"}, at_two) is False


def test_quiet_hours_stop_the_flashing_but_not_the_colours(server):
    call, engine, light, _ = server
    call("PUT", "/api/settings", {"quiet_hours": {"enabled": True, "from": "00:00", "to": "23:59", "mode": "no_flash"}})
    assert call("GET", "/api/state")[1]["quiet_now"] is True
    before = len(light.colors)
    call("POST", "/api/rules/preview", [{"device": "light", "effect": "flash", "color": [255, 0, 0], "count": 2}])
    assert len(light.colors) == before                       # nothing flashed
    call("POST", "/api/rules/preview", [{"device": "light", "effect": "set", "color": [0, 255, 0]}])
    assert light.colors[-1] == (0, 255, 0)                   # the status colour still lands

    call("PUT", "/api/settings", {"quiet_hours": {"enabled": True, "from": "00:00", "to": "23:59", "mode": "dark"}})
    call("POST", "/api/rules/preview", [{"device": "light", "effect": "set", "color": [0, 255, 0]}])
    assert light.colors[-1] == (0, 0, 0)


def test_log_tail_returns_the_end_of_the_log(server, tmp_path, monkeypatch):
    call, *_ = server
    from lumen import paths
    from lumen.server import api
    log = tmp_path / "lumen.log"
    monkeypatch.setattr(paths, "log_file", lambda: log)
    assert api.log_tail() == []                                 # no file yet
    log.write_text("\n".join(f"line {i}" for i in range(500)) + "\n", encoding="utf-8")
    tail = api.log_tail(max_lines=3)
    assert tail == ["line 497", "line 498", "line 499"]
    status, out = call("GET", "/api/log")
    assert status == 200 and out["lines"][-1] == "line 499" and len(out["lines"]) == 300


def test_quiet_hours_settings_are_validated(tmp_path):
    config = Config(tmp_path / "c.json")
    for bad in ({"from": "25:00"}, {"to": "7"}, {"mode": "strobe"}, "nope"):
        with pytest.raises(ValueError):
            config.update_settings({"quiet_hours": bad})
    saved = config.update_settings({"quiet_hours": {"enabled": True, "from": "22:30"}})["quiet_hours"]
    assert saved == {"enabled": True, "from": "22:30", "to": "07:00", "mode": "no_flash"}


def test_a_settings_file_from_another_version_still_starts(tmp_path):
    path = tmp_path / "c.json"
    path.write_text('{"settings": {"port": 99999, "quiet_hours": "nonsense", "gone": 1}}')
    settings = Config(path).settings
    assert settings["port"] == 6733 and settings["quiet_hours"]["enabled"] is False


def test_repaint_puts_the_base_colour_back(server):
    call, engine, light, _ = server
    call("POST", "/api/rules/preview", [{"device": "light", "effect": "set", "color": [10, 20, 30]}])
    light.colors.clear()
    engine.player.repaint()
    assert light.colors == [(10, 20, 30)]
    assert isinstance(engine.player, EffectPlayer)
