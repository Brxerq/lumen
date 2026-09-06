"""Sound as a device.

The four notification tones are synthesised here rather than shipped as audio
files. Nothing is downloaded, nothing is licensed from anyone, and every machine
gets the identical sound instead of whatever its desktop theme happens to have —
a MacBook's Glass and Windows' SystemAsterisk are not the same signal. They are
written to the data directory once, as small 16-bit WAVs, and played back with
whatever the platform already has.

The four OS-theme names Lumen shipped before (`default`, `success`, `error`,
`attention`) still resolve, so existing rules keep working.

Volume is baked into the rendered WAV rather than set on a mixer: Windows'
winsound has no volume knob at all, and per-application volume elsewhere means
talking to the sound server. One small file per (tone, volume) needs no audio
API to exist.
"""

from __future__ import annotations

import math
import shutil
import struct
import subprocess
import sys
import threading
import wave
from pathlib import Path

from lumen.core.devices import SOUND, Device

RATE = 44100

# name -> (label, [(frequency Hz, seconds[, decay]), ...]). Partials are pure
# sines with a short fade at each end; anything sharper than that clicks on
# cheap speakers. A partial marked `decay` also fades out exponentially across
# its whole length, which is what makes a struck bell sound struck.
TONES: dict[str, tuple[str, list[tuple]]] = {
    # A rising major third: reads as "done" without sounding like an alert.
    "chime":   ("Chime",   [(587.33, 0.11), (880.00, 0.26)]),
    # One short mid tone. Quiet enough to fire often.
    "blip":    ("Blip",    [(784.00, 0.08)]),
    # Two low taps. Meant for "come back to me", not for celebration.
    "knock":   ("Knock",   [(311.13, 0.09), (0.0, 0.06), (311.13, 0.11)]),
    # A falling minor third. The only one that should feel like bad news.
    "descend": ("Descend", [(466.16, 0.13), (349.23, 0.30)]),
    # A strike partial over a long decaying fundamental: the doorbell shape.
    "bell":    ("Bell",    [(2093.00, 0.05, True), (1046.50, 0.85, True)]),
    # The shortest tone here, for events that fire all day.
    "ping":    ("Ping",    [(1567.98, 0.22, True)]),
    # A major triad played upwards: the one that should feel like good news.
    "fanfare": ("Fanfare", [(523.25, 0.09), (659.25, 0.09), (783.99, 0.45, True)]),
    # Two repeats and a step up, the pattern every alarm clock uses.
    "alert":   ("Alert",   [(880.00, 0.09), (0.0, 0.05), (880.00, 0.09),
                                                (0.0, 0.05), (1108.73, 0.18)]),
}

# What the pre-existing names now play.
ALIASES = {"default": "blip", "success": "chime", "error": "descend", "attention": "knock"}

SOUNDS = tuple(TONES)


def resolve(name: str) -> str:
    """Map any accepted sound name onto a tone. Unknown names fall back to blip."""
    name = (name or "").strip().lower()
    if name in TONES:
        return name
    return ALIASES.get(name, "blip")


def level(volume) -> float:
    """A volume as a 0.05..1 number, rounded to the 5% the dashboard slider uses.

    Rounding is what keeps the cache small: without it, a slider dragged across
    its range would render a hundred near-identical files.
    """
    try:
        volume = float(volume)
    except (TypeError, ValueError):
        return 1.0
    return min(1.0, max(0.05, round(volume * 20) / 20))


def _samples(parts: list[tuple], volume: float = 1.0) -> bytes:
    """Render partials to signed 16-bit mono PCM at `volume` (0..1).

    A 6 ms raised-cosine fade opens and closes every partial. Without it the
    waveform starts at full amplitude and the speaker pops.
    """
    fade = int(RATE * 0.006)
    peak = 0.32 * min(1.0, max(0.0, volume))
    out = bytearray()
    for freq, seconds, *rest in parts:
        total = int(RATE * seconds)
        decays = bool(rest and rest[0])
        for i in range(total):
            if freq <= 0:  # a rest
                out += struct.pack("<h", 0)
                continue
            env = 1.0
            if decays:
                env = math.exp(-4.5 * i / total)
            if i < fade:
                env *= 0.5 - 0.5 * math.cos(math.pi * i / fade)
            elif i > total - fade:
                env *= 0.5 - 0.5 * math.cos(math.pi * (total - i) / fade)
            out += struct.pack("<h", int(peak * env * 32767 * math.sin(2 * math.pi * freq * i / RATE)))
    return bytes(out)


def write_wav(path: Path, parts: list[tuple], volume: float = 1.0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")  # a half-written file must never be played
    with wave.open(str(tmp), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(_samples(parts, volume))
    tmp.replace(path)
    return path


def tone_file(name: str, volume: float = 1.0) -> Path:
    """Path to a tone's WAV at this volume, rendering it on first use."""
    from lumen import paths
    name = resolve(name)
    volume = level(volume)
    path = paths.data_dir() / "sounds" / f"{name}-{round(volume * 100)}.wav"
    if not path.exists() or path.stat().st_size == 0:
        write_wav(path, TONES[name][1], volume)
    return path


def play_file(path: Path) -> None:
    if sys.platform == "win32":
        import winsound
        winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC)
        return
    if sys.platform == "darwin":
        player = ["afplay", str(path)]
    else:
        exe = shutil.which("paplay") or shutil.which("pw-play") or shutil.which("aplay")
        if not exe:  # no audio stack we can drive; the terminal bell is all that is left
            sys.stdout.write("\a")
            sys.stdout.flush()
            return
        player = [exe, str(path)]
    subprocess.Popen(player, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def play(name: str = "blip", volume: float = 1.0) -> None:
    try:
        play_file(tone_file(name, volume))
    except Exception:  # a missing audio device must never take the daemon down
        pass


class SystemSound(Device):
    def __init__(self):
        super().__init__(id="sound", name="System sound", kind="sound", vendor="",
                         capabilities=frozenset({SOUND}),
                         details={"sounds": list(SOUNDS),
                                  "sound_labels": {k: v[0] for k, v in TONES.items()},
                                  "connection": "built-in"})

    def play_sound(self, name: str, volume: float = 1.0) -> None:
        threading.Thread(target=play, args=(name, volume), daemon=True).start()


def discover() -> list[Device]:
    return [SystemSound()]
