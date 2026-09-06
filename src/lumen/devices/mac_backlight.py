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

from lumen.core.devices import BRIGHTNESS, Device

FRAMEWORK = "/System/Library/PrivateFrameworks/CoreBrightness.framework"
KEYBOARD = 1  # the built-in keyboard; external ones are not addressed by this client


def client():
    """A KeyboardBrightnessClient, or raise if the framework isn't reachable."""
    import objc
    ns: dict = {}
    objc.loadBundle("CoreBrightness", ns, bundle_path=FRAMEWORK)
    return ns["KeyboardBrightnessClient"].alloc().init()


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

    def restore(self, saved) -> None:
        self.set_brightness(float(saved))


def discover() -> list[Device]:
    if sys.platform != "darwin":
        return []
    try:
        device = KeyboardBacklight(client())
    except Exception:  # no pyobjc, or a Mac whose CoreBrightness lacks the client
        return []
    # A Mac with no backlight (a desktop, or an external keyboard only) answers
    # with nothing readable — don't offer a device that cannot light up.
    return [device] if device.remember() is not None else []
