"""Phase 1 hardening: same-origin guard, settings validation, hook safety,
command quoting, dead-tab pruning and log rotation."""

import json
import subprocess
import sys
import time
import urllib.error
import urllib.request

import pytest

from lumen.app.tray import MAX_LOG_BYTES, rotate_log
from lumen.core.config import Config
from lumen.integrations import agent_sessions as ag
from lumen.integrations.terminal import exec_command
from lumen.server.api import _host_is_loopback, _origin_is_local


# --- 1.1 same-origin guard ---------------------------------------------------
@pytest.mark.parametrize("host, ok", [
    ("127.0.0.1:6733", True), ("localhost:6733", True), ("[::1]:6733", True), ("127.0.0.1", True),
    ("evil.example:6733", False), ("evil.example", False), ("127.0.0.1:9999", False), (None, False),
])
def test_host_header(host, ok):
    assert _host_is_loopback(host, 6733) is ok


@pytest.mark.parametrize("origin, ok", [
    (None, True), ("null", False), ("http://127.0.0.1:6733", True), ("http://localhost:6733", True),
    ("https://evil.example", False), ("http://evil.example:6733", False), ("http://127.0.0.1:9999", False),
])
def test_origin_header(origin, ok):
    assert _origin_is_local(origin, 6733) is ok


def _raw(base, method, path, headers, body=None):
    req = urllib.request.Request(base + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


def test_foreign_origin_is_refused(server):
    call, engine, *_ = server
    assert _raw(call.base, "POST", "/api/pause", {"Origin": "https://evil.example"}, {"paused": True}) == 403
    assert _raw(call.base, "GET", "/api/state", {"Origin": "https://evil.example"}) == 403
    assert engine.paused is False  # the engine never saw it
    assert call("POST", "/api/pause", {"paused": False})[0] == 200  # the dashboard still works


def test_opaque_origin_is_refused(server):
    """A sandboxed iframe sends Origin: null — that is not a local page."""
    call, engine, *_ = server
    assert _raw(call.base, "POST", "/api/pause", {"Origin": "null"}, {"paused": True}) == 403
    assert engine.paused is False
    assert _raw(call.base, "POST", "/api/pause", {}, {"paused": True}) == 200  # no Origin (curl) still works
    assert engine.paused is True


# --- 1.1b request body limits ---------------------------------------------------
def _raw_with_content_length(base, path, content_length, body=b""):
    import http.client
    port = int(base.rsplit(":", 1)[1])
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.putrequest("POST", path)
    conn.putheader("Content-Length", content_length)
    conn.endheaders(body)
    resp = conn.getresponse()
    resp.read()
    status = resp.status
    conn.close()
    return status


def test_body_limits_are_enforced(server):
    call, engine, *_ = server
    assert _raw_with_content_length(call.base, "/api/pause", "not-a-number") == 400
    assert _raw_with_content_length(call.base, "/api/pause", "-1") == 400
    assert _raw_with_content_length(call.base, "/api/pause", str(2 * 1024 * 1024)) == 413
    assert engine.paused is False  # none of them reached the engine
    assert call("POST", "/api/pause", {"paused": True})[0] == 200  # and the server is still healthy


# --- 1.1c error responses don't leak local detail ---------------------------------
def test_error_responses_are_generic(server, monkeypatch):
    call, engine, *_ = server

    def boom(_paused):
        raise RuntimeError("C:\\Users\\dev\\private\\notes")

    monkeypatch.setattr(engine, "set_paused", boom)
    assert call("POST", "/api/pause", {"paused": True}) == (500, {"error": "internal error"})

    from lumen.app import update
    def fail():
        raise OSError("C:\\Users\\dev\\private\\notes")
    monkeypatch.setattr(update, "check", fail)
    assert call("GET", "/api/update") == (502, {"error": "update check failed"})


def test_webhook_token_is_the_one_cross_origin_door(server):
    call, engine, *_ = server
    call("PUT", "/api/settings", {"webhook_token": "s3cret"})
    headers = {"Origin": "https://ci.example", "Authorization": "Bearer s3cret"}
    assert _raw(call.base, "POST", "/api/events", headers, {"type": "build.failed"}) == 200
    assert _raw(call.base, "POST", "/api/events", {"Origin": "https://ci.example"}, {"type": "x"}) == 403
    assert _raw(call.base, "POST", "/api/pause", headers, {"paused": True}) == 403  # token is not a master key


def _raw_get(base, path):
    """A GET whose path reaches the server verbatim — urllib would tidy the
    dot segments away before they ever got there."""
    import http.client
    conn = http.client.HTTPConnection("127.0.0.1", int(base.rsplit(":", 1)[1]), timeout=5)
    conn.putrequest("GET", path, skip_accept_encoding=True)
    conn.endheaders()
    resp = conn.getresponse()
    body = resp.read()
    conn.close()
    return resp.status, body


def test_static_files_stay_inside_the_ui_directory(server):
    call, *_ = server
    from lumen.server import api
    # A sibling directory whose name merely *starts with* the ui/ path is not
    # inside it — which is exactly what a string prefix test cannot tell.
    sibling = api.UI_DIR.parent / (api.UI_DIR.name + "-secret")
    sibling.mkdir(exist_ok=True)
    (sibling / "notes.txt").write_text("private")
    try:
        for path in ("/../api.py", f"/../{sibling.name}/notes.txt"):
            status, body = _raw_get(call.base, path)
            assert (status, b"private" in body) == (404, False), path
    finally:
        (sibling / "notes.txt").unlink()
        sibling.rmdir()
    assert _raw_get(call.base, "/app.js")[0] == 200  # the real dashboard still loads


# --- 1.2 adapter whitelist ---------------------------------------------------
def test_unknown_adapter_is_not_imported(server):
    call, *_ = server
    assert call("POST", "/api/adapters/notification/nope", {})[0] == 404
    assert call("POST", "/api/adapters/os/system", {})[0] == 404


def test_device_ids_are_safe_whoever_named_them():
    """A Govee light announces its own id over multicast, so anything on the
    LAN can name a device. That name reaches a config key, a URL path and the
    dashboard's inline click handlers, so the charset is pinned centrally."""
    from lumen.core.devices import Device
    hostile = Device(id="govee-x'),alert(1),('", name="x")
    assert hostile.id == "govee-x-alert-1"
    assert Device(id="../../etc/passwd", name="x").id == "etc-passwd"
    assert Device(id="!!!", name="x").id == "device"
    assert Device(id="asus-aura-keyboard", name="x").id == "asus-aura-keyboard"  # ordinary ids untouched


# --- 1.3 settings validation -------------------------------------------------
def test_settings_reject_unusable_values(tmp_path, server):
    call, engine, *_ = server
    assert call("PUT", "/api/settings", {"port": 5})[0] == 400
    assert call("PUT", "/api/settings", {"port": 70000})[0] == 400
    assert call("PUT", "/api/settings", {"rescan_interval_s": -1})[0] == 400
    assert engine.config.settings["port"] == 6733  # unchanged
    assert call("PUT", "/api/settings", {"port": 6800})[1]["port"] == 6800

    config = Config(tmp_path / "c.json")
    with pytest.raises(ValueError):
        config.update_settings({"port": "not a number"})


def test_server_falls_back_to_the_next_free_port(server):
    from lumen.server.api import serve_in_background
    call, engine, *_ = server
    busy = int(call.base.rsplit(":", 1)[1])
    second = serve_in_background(engine, busy)
    try:
        assert second.server_address[1] != busy
    finally:
        second.shutdown()


# --- 1.4 hook install safety -------------------------------------------------
def test_install_hooks_is_idempotent(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"model": "opus", "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "mine.sh"}]}]}}))
    ag.install_hooks(path, {"Stop": None}, command="lumen hook")
    ag.install_hooks(path, {"Stop": None}, command="lumen hook")
    data = json.loads(path.read_text())
    commands = [h["command"] for g in data["hooks"]["Stop"] for h in g["hooks"]]
    assert commands.count("lumen hook") == 1
    assert "mine.sh" in commands and data["model"] == "opus"  # nothing of the user's was lost
    assert ag.uninstall_hooks(path) and json.loads(path.read_text())["hooks"]["Stop"]


def test_install_hooks_refuses_a_broken_settings_file(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text('{"hooks": {,,,}')
    with pytest.raises(ValueError):
        ag.install_hooks(path, {"Stop": None}, command="lumen hook")
    assert path.read_text() == '{"hooks": {,,,}'  # untouched

    integ = ag.AgentIntegration(lambda e: None, {})
    integ.hooks_file, integ.hooks = path, {"Stop": None}
    assert "not valid JSON" in integ.connect()
    assert "not valid JSON" in integ.disconnect()


def test_empty_settings_file_is_fine(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("   \n")
    ag.install_hooks(path, {"Stop": None}, command="lumen hook")
    assert json.loads(path.read_text())["hooks"]["Stop"]


def test_only_lumens_own_hook_is_stripped(tmp_path):
    path = tmp_path / "settings.json"
    theirs = "notify lumen of hook events.sh"  # both words, not our command
    ours = ['lumen hook', '"/opt/Lumen/lumen.exe" hook', '"C:\\Python312\\python.exe" -m lumen hook']
    path.write_text(json.dumps({"hooks": {"Stop": [{"hooks": [{"type": "command", "command": c}]} for c in [theirs, *ours]]}}))
    assert ag.hooks_installed(path)
    assert ag.uninstall_hooks(path)
    data = json.loads(path.read_text())
    assert [h["command"] for g in data["hooks"]["Stop"] for h in g["hooks"]] == [theirs]
    assert not ag.hooks_installed(path)

    path.write_text(json.dumps({"hooks": {"Stop": [{"hooks": [{"type": "command", "command": theirs}]}]}}))
    assert not ag.hooks_installed(path)
    assert ag.uninstall_hooks(path) is False  # nothing of ours: file untouched


# --- 1.5 command quoting -----------------------------------------------------
def test_exec_command_survives_spaces_in_arguments(monkeypatch):
    sent = {}
    monkeypatch.setattr("lumen.integrations.terminal.post_event",
                        lambda t, d, p, tok: sent.update(type=t, data=d) or True)
    argv = [sys.executable, "-c", "import sys; print(sys.argv[1])", "a b"]
    out = subprocess.run(
        subprocess.list2cmdline(argv) if sys.platform == "win32" else argv,
        shell=sys.platform == "win32", capture_output=True, text=True)
    assert out.stdout.strip() == "a b"
    assert exec_command(argv, 6733) == 0
    assert sent["type"] == "command.succeeded"


def test_exec_strips_only_the_leading_separator(monkeypatch):
    from lumen import cli
    seen = {}
    monkeypatch.setattr("lumen.integrations.terminal.exec_command",
                        lambda argv, port, tok: seen.update(argv=argv) or 0)
    monkeypatch.setattr(cli, "_endpoint", lambda: (6733, ""))
    assert cli.main(["exec", "--", "build.sh", "--", "--watch"]) == 0
    assert seen["argv"] == ["build.sh", "--", "--watch"]


# --- 1.6 dead tabs ------------------------------------------------------------
def test_prune_gone_drops_tabs_the_agent_forgot():
    statuses = {"live": ag.RUNNING, "closed": ag.RUNNING, "fresh": ag.RUNNING}
    truth = {"live": True}
    records = {"live": {"ts": 1000.0}, "closed": {"ts": 500.0}, "fresh": {"ts": 999.0}}
    kept = ag.prune_gone(statuses, truth, records, now=1000.0, grace_s=120)
    assert set(kept) == {"live", "fresh"}  # "closed" is older than the grace window
    # With no record of our own to compare against, nothing is dropped.
    assert ag.prune_gone(statuses, {}, records, now=1000.0) == statuses


def test_current_sessions_forgets_the_file_of_a_closed_tab(tmp_path, monkeypatch):
    import time
    monkeypatch.setenv("LUMEN_HOME", str(tmp_path))
    sessions = tmp_path / "sessions"
    now = time.time()
    for sid, ts in (("live", now), ("gone", now - 3600)):
        ag.apply_hook({"hook_event_name": "UserPromptSubmit", "session_id": sid, "cwd": "/p"}, sessions, now=ts)
    integ = ag.AgentIntegration(lambda e: None, {})
    integ.agent = "claude"
    integ.truth = lambda hooked: {"live": True}  # the agent only knows about the open tab
    assert integ.current_sessions() == {"live": ag.RUNNING}
    assert not (sessions / "gone.json").exists()
    assert (sessions / "live.json").exists()


# --- 1.7 log rotation ---------------------------------------------------------
def test_rotate_log_keeps_one_generation(tmp_path):
    log = tmp_path / "lumen.log"
    log.write_text("the lines you wanted")
    rotate_log(log)
    assert (tmp_path / "lumen.log.1").read_text() == "the lines you wanted"
    assert not log.exists()
    assert MAX_LOG_BYTES > 0


# --- 4.x hardware recovery ----------------------------------------------------
def test_a_write_error_triggers_a_rescan_and_shows_up_on_the_device(server):
    call, engine, light, _ = server
    scans = []
    engine._discover = lambda settings: scans.append(1) or [light]
    engine.player.on_device_error(light, OSError("device disappeared"))
    for _ in range(50):
        if scans:
            break
        time.sleep(0.05)
    assert scans, "a failing write should rescan instead of waiting out the interval"
    assert "device disappeared" in light.details["last_error"]
    assert call("GET", "/api/state")[1]["messages"][0]["text"].startswith("Fake light disconnected")


def test_transient_effects_force_a_full_repaint_when_they_end():
    """Direct-mode hardware forgets its colour, so the tracked "last write" is
    not proof of what is on screen once an effect finishes. The player drops
    that memory when a transient ends, and writes the base again."""
    from lumen.core.effects import EffectPlayer
    from lumen.core.rules import Action
    from tests.conftest import Light

    light = Light()
    player = EffectPlayer()          # not started: this test drives the ticks
    player.set_devices([light])
    player.run(Action.from_dict({"device": "light", "effect": "set", "color": [0, 40, 0]}))
    player.run(Action.from_dict({"device": "light", "effect": "flash", "color": [255, 0, 0],
                                 "count": 1, "duration": 0.05}))
    player._tick(time.monotonic() + 1.0)          # the effect is over
    assert light.colors[-1] == (0, 40, 0)
    assert "light" not in player._last_write or player._last_write["light"] == ((0, 40, 0),)
