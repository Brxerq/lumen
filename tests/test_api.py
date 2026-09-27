"""The JSON API end to end against a real engine with fake devices.

The `server` fixture (engine + HTTP server + fake devices) lives in conftest.py.
"""

import json
import urllib.error
import urllib.request


def test_state_and_static(server):
    call, engine, light, toast = server
    status, st = call("GET", "/api/state")
    assert status == 200
    assert {d["id"] for d in st["devices"]} == {"light", "notification"}
    assert st["integrations"][0]["id"] == "src" and "agent.finished" in st["catalog"] and "flash" in st["effects"]
    page = urllib.request.urlopen(call.base + "/").read()
    assert b"<title>Lumen</title>" in page
    assert urllib.request.urlopen(call.base + "/app.js").headers["Content-Type"].startswith(("text/javascript", "application/javascript"))
    assert call("GET", "/../pyproject.toml")[0] == 404


def test_event_matches_default_rules_and_logs(server):
    call, engine, light, toast = server
    status, ev = call("POST", "/api/events", {"type": "agents.status", "data": {"status": "input"}})
    assert status == 200 and ev["type"] == "agents.status"
    assert light.colors[-1] == (255, 0, 0)  # "Agent needs you" -> red
    _, st = call("GET", "/api/state")
    assert st["activity"][0]["rules"] == ["Agent needs you"]
    assert st["devices"][0]["color"] == [255, 0, 0]
    assert call("POST", "/api/events", {"data": {}})[0] == 400


def test_webhook_token(server):
    call, engine, *_ = server
    assert call("PUT", "/api/settings", {"webhook_token": "s3cret"})[1]["webhook_token"] == "s3cret"
    assert call("POST", "/api/events", {"type": "x"})[0] == 401
    assert call("POST", "/api/events", {"type": "x"}, token="s3cret")[0] == 200


def test_rules_crud_and_preview(server):
    call, engine, light, toast = server
    rule = {"name": "toast it", "when": "build.failed", "actions": [{"device": "notification", "effect": "notify", "message": "oops {name}"}]}
    status, saved = call("POST", "/api/rules", rule)
    assert status == 200 and saved["id"]
    call("POST", "/api/events", {"type": "build.failed", "data": {"name": "web"}})
    import time
    time.sleep(0.1)
    assert toast.notes[-1] == "oops web"
    status, r = call("POST", "/api/rules/preview", [{"device": "light", "effect": "set", "color": [1, 2, 3]}])
    assert r["touched"] == ["light"] and light.colors[-1] == (1, 2, 3)
    assert call("DELETE", f"/api/rules/{saved['id']}")[1]["deleted"] is True
    assert all(r["id"] != saved["id"] for r in call("GET", "/api/rules")[1])
    assert call("PUT", "/api/rules", [])[1] == []


def test_device_options_test_and_integrations(server):
    call, engine, light, toast = server
    assert call("PATCH", "/api/devices/light", {"name": "Desk lamp", "enabled": False})[1] == {"name": "Desk lamp", "enabled": False}
    _, st = call("GET", "/api/state")
    assert st["devices"][0]["name"] == "Desk lamp" and st["devices"][0]["details"]["enabled"] is False
    assert call("POST", "/api/devices/light/test", {"effect": "flash"})[1]["touched"] == []  # disabled devices don't play
    call("PATCH", "/api/devices/light", {"enabled": True})
    assert call("POST", "/api/devices/light/test", {"effect": "flash"})[1]["touched"] == ["light"]
    assert call("POST", "/api/integrations/src/connect")[1]["message"] == "connected!"
    assert call("POST", "/api/integrations/nope/connect")[0] == 404
    assert call("POST", "/api/adapters/nope/pair")[0] == 404
    assert call("POST", "/api/pause", {"paused": True})[1]["paused"] is True
    assert call("POST", "/api/onboarded")[1]["onboarded"] is True
    assert call("GET", "/api/nothing")[0] == 404


def test_autostart_reports_the_os_not_the_stored_preference(server, monkeypatch):
    """The tray toggles the login entry directly and anything on the machine can
    remove it, so a stored `autostart` is a wish and the registry key or plist is
    the fact. The dashboard has to show the fact, or it disagrees with the tray."""
    call, engine, *_ = server
    from lumen.app import autostart

    real = {"on": False}
    monkeypatch.setattr(autostart, "enabled", lambda: real["on"])
    monkeypatch.setattr(autostart, "set_enabled", lambda v: real.__setitem__("on", v))

    assert call("PUT", "/api/settings", {"autostart": True})[1]["autostart"] is True
    assert real["on"] is True

    # Changed behind the daemon's back — as the tray menu does.
    real["on"] = False
    assert call("GET", "/api/state")[1]["settings"]["autostart"] is False

    # An OS that refuses is an error, not a silently stored preference.
    def refuse(_v):
        raise OSError("access denied")
    monkeypatch.setattr(autostart, "set_enabled", refuse)
    status, body = call("PUT", "/api/settings", {"autostart": True})
    assert status == 500 and "start at login" in body["error"]
    assert call("GET", "/api/state")[1]["settings"]["autostart"] is False


def _raw(call, method, path, data=b"", headers=None):
    req = urllib.request.Request(call.base + path, method=method, data=data,
                                 headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


def test_bad_bodies_are_refused_not_read_as_empty(server):
    """An invalid body used to parse as {}: PUT /api/rules then wiped every rule,
    and a 1e999 in an event made /api/state unparseable for the dashboard."""
    call, engine, *_ = server
    before = call("GET", "/api/rules")[1]
    assert _raw(call, "PUT", "/api/rules", b"{not json") == 400
    assert _raw(call, "PUT", "/api/rules", b'{"id": "x"}') == 400
    assert call("GET", "/api/rules")[1] == before
    assert _raw(call, "POST", "/api/events", b'{"type": "x", "data": {"n": 1e999}}') == 400
    assert _raw(call, "POST", "/api/events", b'{"type": "x", "data": {"n": NaN}}') == 400
    assert call("GET", "/api/state")[0] == 200


def test_forwarded_requests_only_reach_the_webhook(server):
    """Behind a reverse proxy Host reads 127.0.0.1 and there is no Origin."""
    call, engine, *_ = server
    assert _raw(call, "POST", "/api/pause", b'{"paused": true}', {"X-Forwarded-For": "203.0.113.9"}) == 403
    assert not engine.paused


def test_dashboard_test_buttons_work_with_a_webhook_token(server):
    call, engine, *_ = server
    call("PUT", "/api/settings", {"webhook_token": "s3cret"})
    port = call.base.rsplit(":", 1)[1]
    assert _raw(call, "POST", "/api/events", b'{"type": "x"}', {"Origin": f"http://127.0.0.1:{port}"}) == 200
    assert _raw(call, "POST", "/api/events", b'{"type": "x"}', {"Origin": "http://localhost"}) == 403  # port 80 is not us


def test_settings_patch_is_all_or_nothing(server):
    call, engine, *_ = server
    assert call("PUT", "/api/settings", {"notch": False, "port": 99999})[0] == 400
    assert call("GET", "/api/state")[1]["settings"]["notch"] is True


def test_paused_with_keep_lit_holds_the_colour_and_catches_up_on_resume(server):
    call, engine, light, toast = server
    call("PUT", "/api/settings", {"keep_lit": True})
    call("POST", "/api/events", {"type": "agents.status", "data": {"status": "input"}})
    held = light.colors[-1]
    call("POST", "/api/pause", {"paused": True})
    call("POST", "/api/events", {"type": "agents.status", "data": {"status": "done"}})
    assert light.colors[-1] == held  # paused: not reacting
    call("POST", "/api/pause", {"paused": False})
    assert light.colors[-1] != held  # resumed: shows what changed meanwhile


def test_claude_http_hook_writes_the_session_record(server):
    from lumen import paths
    call, *_ = server
    status, body = call("POST", "/api/hook", {"hook_event_name": "UserPromptSubmit", "session_id": "http-s1"})
    assert status == 204 and body is None
    assert json.loads((paths.sessions_dir() / "http-s1.json").read_text())["status"] == "running"
    assert call("POST", "/api/hook", {"hook_event_name": "Bogus", "session_id": "x"})[0] == 204  # never fail the agent
