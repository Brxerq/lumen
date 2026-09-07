"""The parts that only run as an installed app: the tray icon, autostart, the
screen-glow child protocol and the adapter setup endpoint. All faked — none of
these tests touch the registry, the login items or a real display."""

import json
import re
import sys
from unittest import mock

import pytest

from lumen.app import autostart, tray
from lumen.devices import screen

ALLOWED_REPOS = {"Brxerq/lumen"}


# --- tray ---------------------------------------------------------------------
def test_tray_image_paints_the_first_two_device_colours():
    image = tray.tray_image([(255, 0, 0), (0, 0, 255)], size=64)
    assert image.size == (64, 64)
    colors = {c for _, c in image.getcolors(4096)}
    assert (255, 0, 0, 255) in colors and (0, 0, 255, 255) in colors
    # With one device both surfaces show it; with none, the neutral grey.
    assert (0, 255, 0, 255) in {c for _, c in tray.tray_image([(0, 255, 0)]).getcolors(4096)}
    assert tray.BRAND_NEUTRAL + (255,) in {c for _, c in tray.tray_image([]).getcolors(4096)}


def test_shell_colour_follows_the_system_theme():
    tray.shell_color.cache_clear()
    assert tray.shell_color() in (tray.BRAND_INK, tray.BRAND_REVERSED)


def test_single_instance_is_claimed_once(tmp_path, monkeypatch):
    monkeypatch.setenv("LUMEN_HOME", str(tmp_path))
    assert tray.claim_single_instance("lumen-test-instance") is True


def test_redirect_output_to_log_rotates_and_leaves_a_console_alone(tmp_path, monkeypatch):
    assert tray.redirect_output_to_log(tmp_path / "x.log") is False  # a console: leave stdout alone
    log = tmp_path / "lumen.log"
    log.write_bytes(b"." * (tray.MAX_LOG_BYTES + 1))
    monkeypatch.setattr(tray.sys, "stdout", None)
    monkeypatch.setattr(tray.sys, "stderr", None)
    try:
        assert tray.redirect_output_to_log(log) is True
        assert (tmp_path / "lumen.log.1").exists()
    finally:
        tray.sys.stdout.close()


# --- autostart ----------------------------------------------------------------
def test_autostart_command_points_at_something_runnable():
    command = autostart.command()
    assert command and command[0]
    if not getattr(sys, "frozen", False):
        assert command[1:] == ["-m", "lumen"]


@pytest.mark.skipif(sys.platform == "win32", reason="the registry path is covered by its own test")
def test_autostart_writes_and_removes_a_login_entry(tmp_path, monkeypatch):
    entry = tmp_path / ("com.lumen.daemon.plist" if sys.platform == "darwin" else "lumen.desktop")
    monkeypatch.setattr(autostart, "_unix_file", lambda: entry)
    autostart.set_enabled(True)
    assert entry.exists() and "lumen" in entry.read_text()
    autostart.set_enabled(False)
    assert not entry.exists()


@pytest.mark.skipif(sys.platform != "win32", reason="registry")
def test_autostart_uses_the_current_user_run_key(monkeypatch):
    import winreg
    written = {}

    class FakeKey:
        def __enter__(self): return self
        def __exit__(self, *a): return False

    monkeypatch.setattr(winreg, "OpenKey", lambda *a, **k: FakeKey())
    monkeypatch.setattr(winreg, "SetValueEx", lambda key, name, _r, _t, value: written.update({name: value}))
    monkeypatch.setattr(winreg, "DeleteValue", lambda key, name: written.pop(name, None))
    autostart.set_enabled(True)
    assert "lumen" in written["Lumen"].lower()
    autostart.set_enabled(False)
    assert "Lumen" not in written
    assert autostart._RUN_KEY.startswith("Software\\")  # never HKLM: no admin rights needed


# --- screen glow child --------------------------------------------------------
def test_screen_child_parses_one_colour_per_line():
    """The parent writes "r g b" lines to the child's stdin. A malformed line
    must be ignored, not fatal: the parent reads a dead child as "respawn"."""
    assert [screen.parse_line(x) for x in ("255 0 0", "0 0 0", "12 34 56 78")] == [(255, 0, 0), (0, 0, 0), (12, 34, 56)]
    assert [screen.parse_line(x) for x in ("nonsense", "", "1 2", "1 2 x")] == [None, None, None, None]
    assert screen.parse_line("999 -5 0") == (255, 0, 0)


# --- notch status tab child -------------------------------------------------------
def test_notch_child_parses_zones_and_the_session_list():
    from lumen.devices import notch
    assert notch.parse_zones("255 0 0", n=3) == [(255, 0, 0)] * 3
    assert notch.parse_zones("1 2 3 4 5 6 7", n=3) == [(1, 2, 3), (4, 5, 6), (4, 5, 6)]  # trailing partial dropped
    assert notch.parse_zones("999 -1 0 0 0 0 0 0 0 0 0 0", n=2) == [(255, 0, 0), (0, 0, 0)]
    assert [notch.parse_zones(x) for x in ("", "1 2", "a b c")] == [None, None, None]
    sessions = [{"id": "b", "agent": "codex", "slot": 1, "status": "input", "cwd": "/w/api", "label": "",
                 "context": {"tokens": 50_000, "window": 200_000}},
                {"id": "a", "agent": "claude", "slot": 0, "status": "running", "cwd": "C:/w/web/", "label": "API refactor"}]
    zones, got = notch.parse_line("1 2 3 | " + json.dumps(sessions), n=2)
    assert zones == [(1, 2, 3)] * 2 and [s["id"] for s in got] == ["b", "a"]
    assert notch.parse_line("1 2 3 | not json", n=1) == ([(1, 2, 3)], [])   # a bad payload never kills the tab
    assert notch.parse_line("nope | []") is None
    # rows follow the zones (slot order); a named tab shows its name, an unnamed one its folder
    assert notch.session_rows(got) == [("claude", "API refactor", "running", None), ("codex", "api", "input", 25)]
    # adjacent zones of one colour are one tab's bar; black is a free seat, not a bar
    assert notch.runs([(1, 1, 1), (1, 1, 1), (0, 0, 0), (2, 2, 2)]) == [((1, 1, 1), 2), ((2, 2, 2), 1)]


def test_notch_renders_both_states_and_obeys_the_setting(monkeypatch):
    from lumen.devices import notch
    palette = {"running": (240, 170, 40), "input": (230, 60, 60)}
    rows = [("claude", "web", "running", 63), ("codex", "api", "input", None)]
    folded = notch.render([(240, 170, 40)] * 6, [], palette, opaque_key=(1, 0, 1), fills=[63])
    panel = notch.render([(240, 170, 40)] * 3 + [(230, 60, 60)] * 3, rows, palette)
    assert folded.size == (notch.WIDTH, notch.HEIGHT) and panel.size[0] == notch.PANEL_WIDTH and panel.size[1] > notch.HEIGHT
    assert folded.getpixel((0, notch.HEIGHT - 1))[:3] == (1, 0, 1)   # rounded corner shows the chroma key
    assert panel.getpixel((0, panel.height - 1))[3] == 0            # ...or real transparency
    d = notch.Notch()
    assert d.id == "notch" and d.zone_count == notch.ZONE_COUNT and d.capabilities == {"color", "zones"} and d.ambient
    sent = []
    d._send = sent.append
    d.set_color((1, 2, 3))
    d.set_zones([(0, 0, 0), (9, 9, 9)])
    assert [x.split(" | ")[0] for x in sent] == ["1 2 3 " * (notch.ZONE_COUNT - 1) + "1 2 3", "0 0 0 9 9 9"]
    assert all(isinstance(json.loads(x.split(" | ")[1]), list) for x in sent)  # the live session list rides along
    # the setting takes the tab down and keeps it down; no child is spawned in tests
    monkeypatch.setattr(notch.Notch, "warm_up", lambda self: None)
    shut = []
    monkeypatch.setattr(notch.Notch, "shutdown", lambda self: shut.append(1))
    assert [x.id for x in notch.discover({"notch": True})] == ["notch"]
    assert notch.discover({"notch": False}) == [] and shut == [1]
    assert [x.id for x in notch.discover()] == ["notch"]


def test_adapter_setup_action_reaches_the_adapter(server, monkeypatch):
    call, engine, *_ = server
    from lumen.devices import hue
    monkeypatch.setattr(hue, "ACTIONS", {"pair": lambda options, body: (f"paired with {body.get('bridge')}", {"bridge": body.get("bridge")})})
    status, out = call("POST", "/api/adapters/hue/pair", {"bridge": "192.168.1.5"})
    assert status == 200 and out["message"] == "paired with 192.168.1.5"
    assert engine.config.device_options("hue")["bridge"] == "192.168.1.5"
    assert call("POST", "/api/adapters/hue/nope", {})[0] == 404


def test_spawn_swapper_waits_for_this_process_then_moves_the_file(tmp_path, monkeypatch):
    from lumen.app import update
    started = {}
    monkeypatch.setattr(update.subprocess, "Popen", lambda argv, **kw: started.update(argv=argv, kw=kw))
    monkeypatch.setattr(update.tempfile, "gettempdir", lambda: str(tmp_path))
    exe, new = tmp_path / "lumen.exe", tmp_path / "lumen.exe.new"
    update._spawn_swapper(exe, new)
    script = next(p for p in tmp_path.iterdir() if p.name.startswith("lumen-update"))
    body = script.read_text()
    assert str(update.os.getpid()) in body and str(exe) in body and str(new) in body
    assert started["argv"][-1] == str(script)


def test_hook_command_quotes_the_interpreter():
    from lumen.integrations.agent_sessions import hook_command
    command = hook_command()
    assert command.startswith('"') and command.endswith(" hook")


def test_marketplace_and_plugin_manifests_are_valid_json():
    from pathlib import Path

    import lumen
    root = Path(lumen.__file__).resolve().parents[2]
    for path in (root / ".claude-plugin" / "marketplace.json", root / "plugin" / ".claude-plugin" / "plugin.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["name"] == "lumen"


def test_no_dead_links_to_the_upstream_repo():
    """Every github.com link in the docs and the code points at this repository."""
    from pathlib import Path

    import lumen
    root = Path(lumen.__file__).resolve().parents[2]
    offenders = []
    for path in list(root.glob("*.md")) + list((root / "docs").glob("*.md")) + list((root / "src").rglob("*.py")) \
            + list((root / "src").rglob("*.js")) + [root / "pyproject.toml"]:
        for number, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            for url in re.findall(r"https://github\.com/([\w.-]+/[\w.-]+)", line):
                if url.rstrip(".") not in ALLOWED_REPOS:
                    offenders.append(f"{path.name}:{number}: {url}")
    assert not offenders, offenders


def test_update_module_targets_this_repository():
    from lumen.app import update
    assert update.REPO == "Brxerq/lumen"
    assert update.DOWNLOAD_PREFIX.startswith("https://github.com/Brxerq/lumen/releases/download/")
    with mock.patch.object(update, "check", return_value={"available": True, "frozen": True, "latest": "9",
                                                          "asset": "https://cdn.example/lumen.exe", "sums": "x"}):
        assert update.apply(lambda: None).startswith("Refusing")


def test_link_preview_points_at_an_image_that_exists_and_is_the_right_shape():
    """A shared link is a 1200x630 card, not the square logo in a wide frame."""
    from pathlib import Path

    import lumen
    root = Path(lumen.__file__).resolve().parents[2]
    head = (root / "docs" / "index.html").read_text(encoding="utf-8")
    urls = set(re.findall(r'(?:property="og:image"|name="twitter:image") content="([^"]+)"', head))
    assert urls, "the site declares no link-preview image"
    for url in urls:
        name = url.rsplit("/", 1)[-1]
        card = root / "docs" / "brand" / "png" / name
        assert card.is_file(), f"{url} does not exist in the repository"
        with open(card, "rb") as f:
            f.seek(16)
            width, height = int.from_bytes(f.read(4), "big"), int.from_bytes(f.read(4), "big")
        assert (width, height) == (1200, 630), f"{name} is {width}x{height}"
    assert '<meta name="twitter:card" content="summary_large_image">' in head
    assert f'content="{width}"' in head and f'content="{height}"' in head
