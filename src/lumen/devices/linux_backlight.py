"""Keyboard backlights exposed by the Linux kernel under /sys/class/leds.

Most laptops (ThinkPad, Dell, Framework, Apple under Linux...) show a
`*kbd_backlight*` LED with `brightness` and `max_brightness` files. No color,
but a brightness that can pulse or blink — enough to say "your task is done".

Writing needs permission. Either run Lumen as a user in the right group or add
a udev rule such as:

    ACTION=="add", SUBSYSTEM=="leds", KERNEL=="*kbd_backlight*", \
      RUN+="/bin/chmod g+w /sys/class/leds/%k/brightness", RUN+="/bin/chgrp input /sys/class/leds/%k/brightness"
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from lumen.core.devices import BRIGHTNESS, Device

LEDS = Path("/sys/class/leds")


class SysfsBacklight(Device):
    def __init__(self, path: Path):
        self._brightness = path / "brightness"
        self._max = int((path / "max_brightness").read_text().strip() or 1)
        writable = os.access(self._brightness, os.W_OK)
        super().__init__(
            id=f"sysfs-{path.name}", name="Keyboard backlight", kind="keyboard", vendor="Linux",
            capabilities=frozenset({BRIGHTNESS}), connected=writable,
            details={"path": str(path), "levels": self._max, "connection": "sysfs",
                     **({} if writable else {"hint": "not writable: add a udev rule (see adapter docs)"})},
        )

    def set_brightness(self, level: float) -> None:
        self._brightness.write_text(str(round(min(1.0, max(0.0, level)) * self._max)))

    def remember(self):
        try:
            return int(self._brightness.read_text().strip())
        except (OSError, ValueError):
            return None

    def restore(self, saved) -> None:
        self._brightness.write_text(str(saved))


def discover() -> list[Device]:
    if not sys.platform.startswith("linux") or not LEDS.exists():
        return []
    out = []
    for path in sorted(LEDS.glob("*kbd_backlight*")):
        try:
            out.append(SysfsBacklight(path))
        except (OSError, ValueError):
            continue
    return out
