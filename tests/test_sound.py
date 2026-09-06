import wave

import pytest

from lumen.devices import sound


def test_every_tone_renders_a_playable_wav(tmp_path):
    for name, (label, parts) in sound.TONES.items():
        path = sound.write_wav(tmp_path / f"{name}.wav", parts)
        assert label
        with wave.open(str(path)) as w:
            assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, sound.RATE)
            seconds = w.getnframes() / w.getframerate()
            assert seconds == pytest.approx(sum(p[1] for p in parts), abs=0.01)
            frames = w.readframes(w.getnframes())
        # Silence would be a valid WAV and an inaudible notification.
        assert max(frames) > 0
        # Both ends must be faded, or the speaker pops on every play.
        assert frames[:2] == b"\x00\x00" and frames[-2:] == b"\x00\x00"


def test_a_rest_stays_silent(tmp_path):
    path = sound.write_wav(tmp_path / "rest.wav", [(0.0, 0.02)])
    with wave.open(str(path)) as w:
        assert set(w.readframes(w.getnframes())) == {0}


def test_old_sound_names_still_resolve():
    # Rules written before the tones existed hold these names.
    assert sound.resolve("success") == "chime"
    assert sound.resolve("error") == "descend"
    assert sound.resolve("attention") == "knock"
    assert sound.resolve("default") == "blip"
    # A tone name maps to itself; anything unknown lands on something audible.
    for name in sound.SOUNDS:
        assert sound.resolve(name) == name
    assert sound.resolve("nonsense") in sound.TONES
    assert sound.resolve("") in sound.TONES


def test_tone_file_is_written_once_and_reused(tmp_path, monkeypatch):
    monkeypatch.setattr("lumen.paths.data_dir", lambda: tmp_path)
    first = sound.tone_file("chime")
    assert first.exists() and first.stat().st_size > 0
    stamp = first.stat().st_mtime_ns
    assert sound.tone_file("chime") == first
    assert first.stat().st_mtime_ns == stamp  # not re-rendered
    # A truncated file (interrupted write, full disk) is rendered again.
    first.write_bytes(b"")
    assert sound.tone_file("chime").stat().st_size > 0


def test_adapter_publishes_its_tones_for_the_dashboard():
    device = sound.discover()[0]
    details = device.to_dict()["details"]
    assert details["sounds"] == list(sound.SOUNDS)
    assert set(details["sound_labels"]) == set(sound.SOUNDS)


def test_volume_scales_the_samples_and_caches_per_level(tmp_path, monkeypatch):
    monkeypatch.setattr("lumen.paths.data_dir", lambda: tmp_path)
    loud = sound.tone_file("chime", 1.0)
    quiet = sound.tone_file("chime", 0.2)
    assert loud != quiet  # one file per volume, so neither has to be re-rendered
    peak = lambda path: max(abs(int.from_bytes(f, "little", signed=True))
                            for f in _frames(path))
    assert 0 < peak(quiet) < peak(loud)
    # The slider steps in 5%, and nothing may reach silence or clip.
    assert sound.level(0.23) == 0.25
    assert sound.level(0) == 0.05 and sound.level(9) == 1.0
    assert sound.level("loud") == 1.0


def _frames(path):
    with wave.open(str(path)) as w:
        data = w.readframes(w.getnframes())
    return [data[i:i + 2] for i in range(0, len(data), 2)]


def test_a_decaying_partial_fades_out(tmp_path):
    path = sound.write_wav(tmp_path / "bell.wav", [(880.0, 0.4, True)])
    frames = _frames(path)
    loudest = lambda chunk: max(abs(int.from_bytes(f, "little", signed=True)) for f in chunk)
    third = len(frames) // 3
    assert loudest(frames[:third]) > loudest(frames[-third:]) * 3


def test_play_never_raises_without_an_audio_device(monkeypatch):
    monkeypatch.setattr(sound, "tone_file", lambda name: (_ for _ in ()).throw(OSError("no disk")))
    sound.play("chime")  # the daemon must survive a broken sound stack
