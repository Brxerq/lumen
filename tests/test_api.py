"""The JSON API end to end against a real engine with fake devices.

The `server` fixture (engine + HTTP server + fake devices) lives in conftest.py.
"""

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
