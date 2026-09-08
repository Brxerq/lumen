"""Self-update: version comparison and the API's refusal paths (no network)."""

import shlex
from pathlib import Path
from unittest import mock

from lumen.app import update


def test_unix_swapper_quotes_paths_and_uses_unique_scripts(tmp_path, monkeypatch):
    exe = tmp_path / "Pat's $(touch injected) Lumen"
    new = exe.with_suffix(".new")
    monkeypatch.setattr(update.sys, "platform", "darwin")
    monkeypatch.setattr(update.tempfile, "gettempdir", lambda: str(tmp_path))
    with mock.patch.object(update.subprocess, "Popen") as popen:
        update._spawn_swapper(exe, new)
        update._spawn_swapper(exe, new)
    scripts = [Path(call.args[0][1]) for call in popen.call_args_list]
    for script in scripts:
        lines = script.read_text(encoding="utf-8").splitlines()
        move = shlex.split(next(line for line in lines if line.startswith("mv ")))
        assert move[:7] == ["mv", "-f", str(new), str(exe), "&&", "chmod", "+x"]
        assert move[7] == str(exe)
        launch = shlex.split(next(line for line in lines if "nohup " in line))
        assert launch[1] == str(exe)
        stop = shlex.split(next(line for line in lines if "pkill " in line))
        assert stop == ["pkill", "-f", str(exe)]
        assert shlex.split(lines[-1]) == ["rm", "-f", str(script)]
    assert scripts[0] != scripts[1]


def test_release_asset_matches_mac_architecture():
    for machine, expected in [("arm64", "lumen-macos"), ("x86_64", "lumen-macos-x86_64"), ("unknown", "")]:
        with mock.patch.object(update.sys, "platform", "darwin"), mock.patch("platform.machine", return_value=machine):
            assert update._platform_asset() == expected
    for system, expected in [("win32", "lumen.exe"), ("linux", "lumen-linux")]:
        with mock.patch.object(update.sys, "platform", system):
            assert update._platform_asset() == expected


def test_version_tuple_ordering():
    assert update._vtuple("v0.3.0") > update._vtuple("0.2.0")
    assert update._vtuple("0.2.0") == update._vtuple("v0.2.0")
    assert update._vtuple("0.10.0") > update._vtuple("0.9.9")
    assert update._vtuple("1.0.0-rc1") == (1, 0, 0)


def test_apply_refuses_when_up_to_date_or_not_frozen():
    calls = []
    with mock.patch.object(update, "check", return_value={"available": False, "frozen": True}):
        assert update.apply(calls.append) == "Already up to date."
    with mock.patch.object(update, "check", return_value={"available": True, "frozen": False, "asset": "x", "latest": "9.9.9"}):
        assert "pip install --upgrade" in update.apply(calls.append)
    with mock.patch.object(update, "check", return_value={"available": True, "frozen": True, "asset": "https://evil.example/lumen.exe", "latest": "9.9.9"}):
        assert update.apply(calls.append).startswith("Refusing")
    assert calls == []  # never asked the daemon to exit


def test_apply_refuses_without_a_published_checksum():
    info = {"available": True, "frozen": True, "latest": "9.9.9",
            "asset": update.DOWNLOAD_PREFIX + "v9.9.9/" + update.ASSET, "sums": None}
    with mock.patch.object(update, "check", return_value=info):
        assert "cannot be verified" in update.apply(lambda: None)


def test_expected_digest_reads_the_line_for_this_platform():
    body = ("b" * 64 + "  something-else\n" + "a" * 64 + f"  {update.ASSET}\n").encode()
    with mock.patch.object(update.urllib.request, "urlopen", _fake_response(body)):
        assert update._expected_digest(update.DOWNLOAD_PREFIX + "v1/SHA256SUMS") == "a" * 64
    # A checksum file hosted anywhere else is not consulted at all.
    assert update._expected_digest("https://evil.example/SHA256SUMS") == ""


def test_apply_deletes_a_download_that_fails_its_checksum(tmp_path, monkeypatch):
    exe = tmp_path / "lumen.exe"
    exe.write_bytes(b"old")
    payload = b"x" * 1_100_000
    info = {"available": True, "frozen": True, "latest": "9.9.9",
            "asset": update.DOWNLOAD_PREFIX + "v9.9.9/" + update.ASSET,
            "sums": update.DOWNLOAD_PREFIX + "v9.9.9/SHA256SUMS"}
    monkeypatch.setattr(update.sys, "executable", str(exe))
    swapped = []
    with mock.patch.object(update, "check", return_value=info), \
         mock.patch.object(update, "_expected_digest", return_value="0" * 64), \
         mock.patch.object(update, "_spawn_swapper", lambda *a: swapped.append(a)), \
         mock.patch.object(update.urllib.request, "urlopen", _fake_response(payload)):
        assert "does not match the checksum" in update.apply(lambda: None)
    assert not swapped and not (tmp_path / "lumen.exe.new").exists()


def _fake_response(body: bytes):
    import io
    from contextlib import contextmanager

    @contextmanager
    def urlopen(*args, **kwargs):
        yield io.BytesIO(body)

    return urlopen
