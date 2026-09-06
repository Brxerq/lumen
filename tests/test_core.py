import time

from lumen.core import effects
from lumen.core.config import Config
from lumen.core.devices import BRIGHTNESS, COLOR, NOTIFY, SOUND, ZONES, Device
from lumen.core.effects import EffectPlayer
from lumen.core.events import Event, EventBus
from lumen.core.rules import Action, Rule, default_rules


class FakeLight(Device):
    def __init__(self, id="light", caps=(COLOR, ZONES), zones=3):
        super().__init__(id=id, name=id, kind="strip", capabilities=frozenset(caps), zone_count=zones)
        self.colors, self.zones, self.levels = [], [], []

    def set_color(self, rgb):
        self.colors.append(tuple(rgb))

    def set_zones(self, colors):
        self.zones.append([tuple(c) for c in colors])

    def set_brightness(self, level):
        self.levels.append(level)


class FakeToaster(Device):
    def __init__(self):
        super().__init__(id="notification", name="toast", kind="notification", capabilities=frozenset({NOTIFY}))
        self.notes = []

    def notify(self, title, message):
        self.notes.append((title, message))


def test_event_bus_fans_out_and_survives_bad_subscriber():
    bus = EventBus(history=3)
    got = []
    bus.subscribe(lambda e: 1 / 0)
    bus.subscribe(got.append)
    for i in range(4):
        bus.emit(Event(f"t.{i}"))
    assert [e.type for e in got] == ["t.0", "t.1", "t.2", "t.3"]
    assert [e.type for e in bus.recent()] == ["t.1", "t.2", "t.3"]


def test_rule_matching():
    r = Rule(when="agent.finished", match={"agent": "claude"})
    assert r.matches(Event("agent.finished", data={"agent": "claude"}))
    assert not r.matches(Event("agent.finished", data={"agent": "codex"}))
    assert not r.matches(Event("agent.running", data={"agent": "claude"}))
    assert Rule(when="build.*").matches(Event("build.failed"))
    assert not Rule(when="build.*", enabled=False).matches(Event("build.failed"))
    assert Rule(when="x", match={"k": ""}).matches(Event("x"))  # empty filter = any


def test_action_and_rule_roundtrip_and_sanitising():
    a = Action.from_dict({"effect": "bogus", "color": "#ff8000", "count": 0, "duration": -1, "brightness": 4})
    assert (a.effect, a.color, a.count, a.duration, a.brightness) == ("flash", (255, 128, 0), 1, 0.1, 1.0)
    d = {"id": "r1", "name": "n", "when": "build.failed", "match": {"name": "web", "empty": ""}, "actions": [a.to_dict()]}
    r = Rule.from_dict(d)
    assert r.match == {"name": "web"} and r.to_dict()["actions"][0]["color"] == [255, 128, 0]
    assert all(isinstance(x, Rule) for x in default_rules())


def test_envelopes():
    assert effects.envelope("flash", 0.25) == 1.0 and effects.envelope("flash", 0.75) == 0.0
    assert abs(effects.envelope("pulse", 0.5) - 1.0) < 1e-9 and effects.envelope("pulse", 0.0) == 0.0
    zones = effects.wave_zones(3, 0.5, (255, 255, 255), (0, 0, 0))
    assert len(zones) == 3 and max(z[0] for z in zones) > 0


def test_player_targets_and_degrades():
    light, back, toast = FakeLight(), FakeLight("backlight", caps=(BRIGHTNESS,), zones=1), FakeToaster()
    p = EffectPlayer()
    p.set_devices([light, back, toast])
    flash = Action(device="*", effect="flash", color=(0, 255, 0))
    assert sorted(d.id for d in p.targets(flash)) == ["backlight", "light"]  # not the toaster
    assert [d.id for d in p.targets(Action(device="notification", effect="notify"))] == ["notification"]
    assert p.targets(Action(device="notification", effect="flash")) == []      # specific but unsupported
    assert [d.id for d in p.targets(Action(device="*", effect="wave"))] == ["light", "backlight"]


def test_player_set_flash_and_restore():
    light = FakeLight()
    p = EffectPlayer(fps=200)
    p.set_devices([light])
    assert p.run(Action(device="light", effect="set", color=(10, 20, 30))) == ["light"]
    assert light.colors[-1] == (10, 20, 30) and p.current_color("light") == (10, 20, 30)
    p.run(Action(device="light", effect="flash", color=(255, 0, 0), count=1, duration=0.2))
    t0 = time.monotonic()
    while time.monotonic() - t0 < 0.5:
        p._tick(time.monotonic())
        time.sleep(0.01)
    assert (255, 0, 0) in light.colors            # the flash was painted
    assert light.colors[-1] == (10, 20, 30)       # and the base came back
    # wave uses zones on a zoned device; brightness devices get a level
    p.run(Action(device="light", effect="wave", color=(0, 0, 255), count=1, duration=0.1))
    p._tick(time.monotonic() + 0.02)
    assert light.zones and len(light.zones[-1]) == 3


def test_flash_without_base_ends_dark_and_repeats():
    # the screen-glow case: no persistent base color, so a flash must return to (0,0,0)
    # through the write tracker — otherwise the next flash's "on" tick is skipped as a no-op.
    screen = FakeLight("screen", caps=(COLOR,), zones=1)
    screen.ambient = False
    p = EffectPlayer(fps=200)
    p.set_devices([screen])
    for _ in range(2):
        screen.colors.clear()
        p.run(Action(device="screen", effect="flash", color=(0, 255, 0), count=2, duration=0.2))
        t0 = time.monotonic()
        while time.monotonic() - t0 < 0.4:
            p._tick(time.monotonic())
            time.sleep(0.005)
        assert (0, 255, 0) in screen.colors          # the flash actually showed green
        assert screen.colors[-1] == (0, 0, 0)        # and ended fully dark, every time
    assert p.current_color("screen") == (0, 0, 0)


def test_player_degrades_to_brightness_and_notifies():
    back, toast = FakeLight("backlight", caps=(BRIGHTNESS,), zones=1), FakeToaster()
    p = EffectPlayer()
    p.set_devices([back, toast])
    p.run(Action(device="*", effect="flash", color=(0, 255, 0), count=1, duration=0.2))
    p._tick(time.monotonic() + 0.02)
    assert back.levels and back.levels[-1] == 1.0
    p.run(Action(device="notification", effect="notify", message="{agent} done"), Event("agent.finished", data={"agent": "claude"}))
    time.sleep(0.05)
    assert toast.notes == [("Lumen · Claude", "claude done")]


def test_sound_plays_at_the_devices_dimmer_and_tolerates_an_old_adapter():
    class Speaker(Device):
        def __init__(self, id, takes_volume):
            super().__init__(id=id, name=id, kind="sound", capabilities=frozenset({SOUND}))
            self.played, self._takes_volume = [], takes_volume

        def play_sound(self, name, *volume):
            if volume and not self._takes_volume:
                raise TypeError("play_sound() takes 2 positional arguments but 3 were given")
            self.played.append((name, volume[0] if volume else None))

    new, old = Speaker("sound", True), Speaker("plugin-speaker", False)
    p = EffectPlayer()
    p.set_devices([new, old])
    p.gain = {"sound": 0.4}          # the Devices page volume slider
    p.run(Action(device="*", effect="sound", sound="bell"))
    time.sleep(0.05)
    assert new.played == [("bell", 0.4)]
    assert old.played == [("bell", None)]  # adapter written before volume existed


def test_player_reduce_flashing_converts_to_pulse():
    light = FakeLight()
    p = EffectPlayer(reduce_flashing=True)
    p.set_devices([light])
    p.run(Action(device="light", effect="flash", color=(255, 255, 255), count=10, duration=1.0))
    active = p._active["light"]
    assert active.effect == "pulse" and active.duration == 5.0  # 10 pulses over >= 5 s = 2 Hz max


def test_player_marks_failing_device_disconnected():
    class Broken(FakeLight):
        def set_color(self, rgb):
            raise OSError("unplugged")

    d = Broken("broken", caps=(COLOR,))
    p = EffectPlayer()
    p.set_devices([d])
    assert p.run(Action(device="broken", effect="set")) == []
    assert d.connected is False


def test_wildcard_persistent_skips_non_ambient_devices():
    screen = FakeLight("screen", caps=(COLOR,), zones=1)
    screen.ambient = False
    light = FakeLight()
    p = EffectPlayer()
    p.set_devices([screen, light])
    assert [d.id for d in p.targets(Action(device="*", effect="set"))] == ["light"]
    assert sorted(d.id for d in p.targets(Action(device="*", effect="flash"))) == ["light", "screen"]
    assert [d.id for d in p.targets(Action(device="screen", effect="set"))] == ["screen"]


def test_engine_replays_base_colors_onto_new_devices(tmp_path):
    from lumen.core.engine import Engine
    first, later, screen = FakeLight("first"), FakeLight("later"), FakeLight("screen", caps=(COLOR,), zones=1)
    screen.ambient = False
    found = [first]
    engine = Engine(Config(tmp_path / "config.json"), integrations=[], discover=lambda s: list(found))
    engine.start()
    engine.emit("agents.status", {"status": "running"})   # default rule: set amber on all
    assert first.colors[-1] == (255, 180, 0)
    found.extend([later, screen])
    engine.scan()
    assert later.colors[-1] == (255, 180, 0)               # newcomer inherits the current base color
    assert screen.colors == []                              # the screen glow stays dark
    engine.stop()


def test_engine_reapplies_edited_rules_immediately(tmp_path):
    from lumen.core.engine import Engine
    light = FakeLight("light")
    engine = Engine(Config(tmp_path / "config.json"), integrations=[], discover=lambda s: [light])
    engine.start()
    engine.emit("agents.status", {"status": "running"})     # default rule: amber everywhere
    assert light.colors[-1] == (255, 180, 0)
    edited = next(r for r in engine.config.rules if r.match.get("status") == "running")
    edited.actions = [Action(device="*", effect="set", color=(0, 0, 255))]
    engine.config.upsert_rule(edited)
    engine.reapply()                                         # no new event: the edit still lands
    assert light.colors[-1] == (0, 0, 255)
    engine.stop()


def test_config_roundtrip(tmp_path):
    c = Config(tmp_path / "config.json")
    assert c.settings["port"] == 6733 and len(c.rules) == 6 and not c.data["onboarded"]
    c.update_settings({"port": "7000", "reduce_flashing": 1, "unknown": True})
    c.upsert_rule(Rule(id="r1", name="one", when="x"))
    c.upsert_rule(Rule(id="r1", name="one-edited", when="x"))
    c.set_device_options("dev", {"enabled": False})
    c.set_onboarded()
    again = Config(tmp_path / "config.json")
    assert again.settings["port"] == 7000 and again.settings["reduce_flashing"] is True and "unknown" not in again.settings
    assert [r.name for r in again.rules if r.id == "r1"] == ["one-edited"]
    assert again.device_options("dev") == {"enabled": False} and again.data["onboarded"]
    assert again.delete_rule("r1") and not again.delete_rule("r1")
    (tmp_path / "config.json").write_text("{broken")
    assert Config(tmp_path / "config.json").settings["port"] == 6733


def test_sessions_effect_paints_one_zone_per_slot():
    light, bulb = FakeLight(), FakeLight("bulb", caps=(COLOR,), zones=1)
    p = EffectPlayer(fps=200)
    p.set_devices([light, bulb])
    ev = Event("agents.sessions", "claude", {"status": "input", "sessions": [
        {"id": "a", "slot": 0, "status": "done"}, {"id": "b", "slot": 1, "status": "input"},
        {"id": "c", "slot": 4, "status": "running"}]})
    assert sorted(p.run(Action(device="*", effect="sessions"), ev)) == ["bulb", "light"]
    assert light.zones[-1] == [(0, 255, 0), (255, 0, 0), (255, 180, 0)]  # slot 4 is busy: borrows the free zone
    assert bulb.colors[-1] == (255, 0, 0)                                 # no zones -> folded status
    assert p.current_zones("light") == [(0, 255, 0), (255, 0, 0), (255, 180, 0)]
    # offset shows later slots; a flash blends over every zone and the zones come back
    p.run(Action(device="light", effect="sessions", offset=4), ev)
    assert light.zones[-1] == [(255, 180, 0), (0, 0, 0), (0, 0, 0)]
    p.run(Action(device="light", effect="flash", color=(0, 0, 255), count=1, duration=0.1))
    p._tick(time.monotonic() + 0.01)
    assert light.zones[-1] == [(0, 0, 255)] * 3
    p._tick(time.monotonic() + 0.2)
    assert light.zones[-1] == [(255, 180, 0), (0, 0, 0), (0, 0, 0)]
    # no sessions at all: the idle colour everywhere, not a dead device
    p.run(Action(device="light", effect="sessions"), Event("agents.sessions", "claude", {"status": "done", "sessions": []}))
    assert light.zones[-1] == [(0, 255, 0)] * 3
    # a custom palette round-trips through from_dict
    a = Action.from_dict({"effect": "sessions", "palette": {"done": "#0000ff"}, "offset": "2"})
    assert a.palette["done"] == (0, 0, 255) and a.palette["input"] == (255, 0, 0) and a.offset == 2
    assert a.to_dict()["palette"]["done"] == [0, 0, 255]


def test_sessions_effect_shows_busy_tabs_beyond_the_last_zone():
    kb = FakeLight("kb", zones=3)
    p = EffectPlayer(fps=200)
    p.set_devices([kb])
    run = lambda sessions: (p.run(Action(device="kb", effect="sessions"),
                                  Event("agents.sessions", "claude", {"status": "input", "sessions": sessions})),
                            kb.zones[-1])[1]
    idle = lambda i: {"id": f"i{i}", "slot": i, "status": "done"}
    # a working tab on slot 5 borrows the last idle zone; a needs-you tab borrows before it
    assert run([idle(0), idle(1), idle(2), {"id": "w", "slot": 5, "status": "running"}]) ==         [(0, 255, 0), (0, 255, 0), (255, 180, 0)]
    assert run([idle(0), idle(1), idle(2), {"id": "w", "slot": 5, "status": "running"},
                {"id": "q", "slot": 7, "status": "input"}]) == [(0, 255, 0), (255, 180, 0), (255, 0, 0)]
    # a free zone is used before an idle one is taken; busy zones are never taken
    assert run([idle(0), {"id": "w", "slot": 4, "status": "running"}]) == [(0, 255, 0), (0, 0, 0), (255, 180, 0)]
    assert run([{"id": "a", "slot": 0, "status": "running"}, {"id": "b", "slot": 1, "status": "input"},
                {"id": "c", "slot": 2, "status": "running"}, {"id": "w", "slot": 9, "status": "input"}]) ==         [(255, 180, 0), (255, 0, 0), (255, 180, 0)]


def test_sessions_effect_agent_filter_and_folded_layout():
    kb, bar = FakeLight("kb", zones=4), FakeLight("bar", zones=2)
    p = EffectPlayer(fps=200)
    p.set_devices([kb, bar])
    ev = Event("agents.sessions", "claude", {"status": "input", "sessions": [
        {"id": "a", "agent": "claude", "slot": 0, "status": "done"},
        {"id": "x", "agent": "codex", "slot": 1, "status": "running"},
        {"id": "b", "agent": "claude", "slot": 2, "status": "input"},
        {"id": "y", "agent": "codex", "slot": 3, "status": "done"}]})
    # keyboard = Claude only, re-ranked so there is no hole where the Codex session sits
    p.run(Action(device="kb", effect="sessions", agent="claude"), ev)
    assert kb.zones[-1] == [(0, 255, 0), (255, 0, 0), (0, 0, 0), (0, 0, 0)]
    # light bar = Codex folded onto the whole device: a running session wins over a done one
    p.run(Action(device="bar", effect="sessions", agent="codex", per_zone=False), ev)
    assert bar.colors[-1] == (255, 180, 0)
    # from_dict normalises the agent name; no open sessions for that agent = idle = the done color
    a = Action.from_dict({"device": "bar", "effect": "sessions", "agent": " Codex ", "per_zone": 0})
    assert a.agent == "codex" and a.per_zone is False
    p.run(a, Event("agents.sessions", "x", {"status": "done", "sessions": []}))
    assert bar.colors[-1] == (0, 255, 0)
    assert effects.agent_sessions(ev.data["sessions"], "codex") == [
        {"id": "x", "agent": "codex", "slot": 0, "status": "running"}, {"id": "y", "agent": "codex", "slot": 1, "status": "done"}]


def test_asus_aura_rescan_reuses_devices(monkeypatch):
    import sys
    import types

    from lumen.devices import asus_aura
    entry = {"path": b"p1", "product_id": 0x19B6, "usage_page": asus_aura.USAGE_PAGE, "usage": asus_aura.USAGE}
    fake_hid = types.SimpleNamespace(enumerate=lambda vid, pid: [entry])
    monkeypatch.setitem(sys.modules, "hid", fake_hid)
    monkeypatch.setattr(asus_aura.sys, "platform", "win32")
    asus_aura._cache.clear()
    first = asus_aura.discover()
    first[0].connected = False
    again = asus_aura.discover()
    assert [d.id for d in first] == ["asus-aura-keyboard", "asus-aura-lightbar"]
    assert again[0] is first[0] and again[1] is first[1] and again[0].connected  # same handle, no re-init flicker
    fake_hid.enumerate = lambda vid, pid: []
    assert asus_aura.discover() == [] and not asus_aura._cache
    fake_hid.enumerate = lambda vid, pid: [entry]
    assert asus_aura.discover()[0] is not first[0]  # replugged: fresh handle
