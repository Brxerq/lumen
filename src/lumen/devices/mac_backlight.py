"""The MacBook keyboard backlight, through the private CoreBrightness framework.

macOS has no public API for the keyboard backlight, but every Mac since the
Intel days ships `KeyboardBrightnessClient` inside CoreBrightness, which is
what the brightness keys and Control Center drive. pyobjc reaches it without a
native helper, and it works the same on Apple Silicon and Intel.

No color — a brightness that can pulse or blink, exactly like the Linux sysfs
backlight. macOS also dims the keyboard on its own from the ambient light
sensor, so a held level may drift; a flash is far shorter than that loop and
shows regardless.
"""

from __future__ import annotations

import sys
from typing import cast

from lumen.core.devices import BRIGHTNESS, Device

FRAMEWORK = "/System/Library/PrivateFrameworks/CoreBrightness.framework"
KEYBOARD = 1  # the built-in keyboard on most Macs; keyboard_id() asks the client for the real one


def client():
    """A KeyboardBrightnessClient, or raise if the framework isn't reachable."""
    import objc
    ns: dict = {}
    objc.loadBundle("CoreBrightness", ns, bundle_path=FRAMEWORK)
    return ns["KeyboardBrightnessClient"].alloc().init()


def keyboard_id(client) -> int:
    """The id of the first backlit keyboard the client knows, or KEYBOARD.

    Recent Apple Silicon Macs don't always number the built-in keyboard 1;
    talking to id 1 there reads back nothing and the device vanished."""
    try:
        ids = [int(i) for i in (client.copyKeyboardBacklightIDs() or [])]
    except Exception:
        ids = []
    return ids[0] if ids else KEYBOARD


class KeyboardBacklight(Device):
    def __init__(self, client, keyboard: int = KEYBOARD):
        self._client, self._keyboard = client, keyboard
        super().__init__(
            id="mac-kbd-backlight", name="Keyboard backlight", kind="keyboard", vendor="Apple",
            capabilities=frozenset({BRIGHTNESS}),
            details={"connection": "CoreBrightness", "keyboard": keyboard},
        )

    def set_brightness(self, level: float) -> None:
        self._client.setBrightness_forKeyboard_(min(1.0, max(0.0, level)), self._keyboard)

    def remember(self):
        try:
            return float(self._client.brightnessForKeyboard_(self._keyboard))
        except Exception:
            return None

    def restore(self, saved: object) -> None:
        if saved is not None:
            self.set_brightness(float(cast(float, saved)))


def discover() -> list[Device]:
    if sys.platform != "darwin":
        return []
    try:
        c = client()
        device = KeyboardBacklight(c, keyboard_id(c))
    except Exception as e:  # no pyobjc, or a Mac whose CoreBrightness lacks the client
        print(f"mac_backlight: CoreBrightness unavailable: {type(e).__name__}: {e}", flush=True)
        return []
    # A Mac with no backlight (a desktop, or an external keyboard only) answers
    # with nothing readable — don't offer a device that cannot light up.
    return [device] if device.remember() is not None else []
