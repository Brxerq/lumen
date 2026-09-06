"""macOS keyboard backlight adapter (CoreBrightness), driven by a fake client."""

import pytest

from lumen.devices import mac_backlight


class FakeClient:
    def __init__(self, level=0.5):
        self.level, self.keyboard = level, None

    def setBrightness_forKeyboard_(self, level, keyboard):
        self.level, self.keyboard = level, keyboard

    def brightnessForKeyboard_(self, keyboard):
        return self.level


@pytest.fixture
def darwin(monkeypatch):
    monkeypatch.setattr(mac_backlight.sys, "platform", "darwin")


def test_brightness_is_clamped_and_addressed_to_the_builtin_keyboard():
    fake = FakeClient()
    d = mac_backlight.KeyboardBacklight(fake)
    d.set_brightness(2.0)
    assert fake.level == 1.0 and fake.keyboard == mac_backlight.KEYBOARD
    d.set_brightness(-1.0)
    assert fake.level == 0.0
    assert d.kind == "keyboard" and d.capabilities == {"brightness"} and d.id == "mac-kbd-backlight"


def test_effect_leaves_the_backlight_where_it_found_it():
    fake = FakeClient(0.027)
    d = mac_backlight.KeyboardBacklight(fake)
    saved = d.remember()
    d.set_brightness(1.0)
    d.restore(saved)
    assert fake.level == pytest.approx(0.027)


def test_discovered_only_on_a_mac_that_answers(monkeypatch, darwin):
    monkeypatch.setattr(mac_backlight, "client", lambda: FakeClient())
    assert [d.id for d in mac_backlight.discover()] == ["mac-kbd-backlight"]

    def unreadable():
        c = FakeClient()
        c.brightnessForKeyboard_ = lambda keyboard: (_ for _ in ()).throw(RuntimeError("no backlight"))
        return c

    monkeypatch.setattr(mac_backlight, "client", unreadable)
    assert mac_backlight.discover() == []          # a Mac with nothing to light
    monkeypatch.setattr(mac_backlight, "client", lambda: (_ for _ in ()).throw(ImportError("no pyobjc")))
    assert mac_backlight.discover() == []          # no pyobjc


def test_not_discovered_off_macos(monkeypatch):
    monkeypatch.setattr(mac_backlight.sys, "platform", "win32")
    monkeypatch.setattr(mac_backlight, "client", lambda: FakeClient())
    assert mac_backlight.discover() == []
