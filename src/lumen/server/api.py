"""Local HTTP server: the dashboard's static files plus a small JSON API.

Bound to 127.0.0.1 only. Standard library, no framework.

    GET  /api/state                          everything the dashboard shows
    POST /api/scan                           rescan devices
    POST /api/pause          {"paused": bool}
    POST /api/devices/<id>/test   {effect, color, count, duration, ...}
    PATCH /api/devices/<id>  {"enabled": bool, "name": "..."}
    POST /api/adapters/<name>/<action>  {...}   adapter-specific setup (e.g. hue/pair)
    GET  /api/rules · PUT /api/rules [..] · POST /api/rules {rule} · DELETE /api/rules/<id>
    POST /api/events         {"type": "...", "data": {...}}   (webhook; bearer token if configured)
    PUT  /api/settings       {partial settings}
    POST /api/integrations/<id>/connect | /disconnect · PATCH /api/integrations/<id> {options}
    POST /api/onboarded
    DELETE /api/sessions/<id>                forget a stale agent session file
    GET  /api/update                         compare the running version with the latest GitHub release
    POST /api/update                         download it and restart (frozen builds only)
    GET  /api/stream                         server-sent events: one line whenever the state changes
    PATCH /api/sessions/<id>  {"slot": n} | {"label": "..."}   which zone a tab owns, and its name
    PUT  /api/sessions/order  [id, ...]      lay tabs out over the zones they occupy, in this order
    GET/POST/DELETE /api/presets             reusable bundles of actions
    DELETE /api/devices/<id>                 forget a disconnected device's saved settings
    DELETE /api/activity                     clear the feed
"""

from __future__ import annotations

import hmac
import json
import mimetypes
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from lumen.core.engine import Engine
from lumen.core.rules import Rule

UI_DIR = Path(__file__).with_name("ui")
LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "[::1]", "::1")
MAX_BODY_BYTES = 1024 * 1024  # no dashboard payload is anywhere near this big


def _session_id(value) -> str:
    """Session ids come from the agents and end up as file names."""
    text = str(value)
    if not text or not text.replace("-", "").replace("_", "").isalnum():
        raise ValueError("bad session id")
    return text


def _host_is_loopback(header: str | None, port: int) -> bool:
    """The Host header a loopback browser sends. Anything else means the request
    arrived through a name that resolves here — a DNS-rebinding attack."""
    if not header:
        return False
    host = header.strip()
    if host.endswith(f":{port}"):
        host = host[: -len(f":{port}")]
    elif ":" in host and not host.startswith("["):
        return False  # a different port is not us
    return host.lower() in LOOPBACK_HOSTS


def _origin_is_local(header: str | None, port: int) -> bool:
    """No Origin (a curl, or a same-origin GET) is fine; a foreign one is not.
    "null" is an opaque origin — a sandboxed iframe — so it counts as foreign."""
    if not header:
        return True
    if header == "null":
        return False
    parsed = urlparse(header)
    if parsed.scheme != "http" or parsed.port not in (port, None):
        return False
    return (parsed.hostname or "").lower() in ("127.0.0.1", "localhost", "::1")



def log_tail(max_lines: int = 300, max_bytes: int = 256 * 1024) -> list[str]:
    """The end of lumen.log, for the Settings page. A windowed build has no
    console, so this is the only place its print() output can be read without
    hunting for the file — and the first thing to look at when a Mac misbehaves."""
    from lumen import paths
    path = paths.log_file()
    try:
        with path.open("rb") as f:
            f.seek(max(0, path.stat().st_size - max_bytes))
            text = f.read().decode("utf-8", "replace")
    except OSError:
        return []
    return text.splitlines()[-max_lines:]

class _Server(ThreadingHTTPServer):
    daemon_threads = True
    # On Windows SO_REUSEADDR lets a second process bind a port that is already
    # in use; the clash then shows up as requests vanishing into the other
    # socket. Only POSIX gets the "reuse a TIME_WAIT port" behaviour we want.
    allow_reuse_address = sys.platform != "win32"


class _Handler(BaseHTTPRequestHandler):
    engine: Engine  # set by make_server
    server_version = "Lumen"
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args) -> None:  # keep the daemon log readable
        pass

    # --- plumbing ------------------------------------------------------------
    def _json(self, status: int, payload) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _repaint_sessions(self) -> None:
        """Rearranging tabs changes no status, so the pollers see nothing new:
        push the snapshot ourselves or the keyboard keeps the old layout until
        some session happens to change state."""
        from lumen.integrations.agent_sessions import aggregate, all_sessions
        sessions = all_sessions()
        self.engine.emit("agents.sessions",
                         {"status": aggregate(s["status"] for s in sessions), "sessions": sessions},
                         source="dashboard")

    def _body(self) -> dict | list | None:
        """The request body as parsed JSON, or None if it was rejected and the
        response has already been sent."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length < 0 or length > MAX_BODY_BYTES:
            self.close_connection = True  # the unread body would poison the next request on this socket
            self._json(400 if length < 0 else 413, {"error": "bad Content-Length" if length < 0 else "body too large"})
            return None
        if not length:
            return {}
        try:
            data = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return {}
        return data if isinstance(data, (dict, list)) else {}

    def _drain(self) -> None:
        """Discard the body of a request we are about to refuse. Leaving it
        unread poisons the next request on a keep-alive socket, and on Windows
        the client gets a connection reset instead of the response."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if 0 < length <= MAX_BODY_BYTES:
            self.rfile.read(length)
        elif length:
            self.close_connection = True

    def _guard(self) -> bool:
        """Reject cross-origin and rebound requests before they touch the engine.

        The API has no login: it is protected by being on loopback. A browser
        can still be told to POST here by any page the user has open, and a
        hostile DNS name can resolve to 127.0.0.1, so check both headers. The
        one endpoint meant to be reached from elsewhere is POST /api/events,
        which authenticates with its own bearer token instead."""
        port = getattr(self.server, "server_port", 0)  # the port actually bound, not the configured one
        if _host_is_loopback(self.headers.get("Host"), port) and _origin_is_local(self.headers.get("Origin"), port):
            return True
        if urlparse(self.path).path == "/api/events" and self._webhook_token_ok():
            return True
        self._drain()
        self._json(403, {"error": "forbidden origin"})
        return False

    def _webhook_token_ok(self) -> bool:
        token = self.engine.config.settings.get("webhook_token")
        return bool(token) and hmac.compare_digest(self.headers.get("Authorization") or "", f"Bearer {token}")

    def _static(self, path: str) -> None:
        name = "index.html" if path in ("", "/") else path.lstrip("/")
        file = (UI_DIR / name).resolve()
        # is_relative_to, not startswith: a sibling directory whose name merely
        # starts with the ui/ path would pass a string prefix test.
        if not file.is_relative_to(UI_DIR.resolve()) or not file.is_file():
            self._json(404, {"error": "not found"})
            return
        body = file.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(str(file))[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        # no-store, not no-cache: these files are a few kilobytes over loopback,
        # and "no-cache" without a validator still leaves browsers serving the old
        # dashboard after an update, which reads as the update not having worked.
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    # --- routing -------------------------------------------------------------
    def do_GET(self):
        path = urlparse(self.path).path
        if not self._guard():
            return
        if path == "/api/state":
            return self._json(200, self.engine.state())
        if path == "/api/rules":
            return self._json(200, [r.to_dict() for r in self.engine.config.rules])
        if path == "/api/update":
            from lumen.app import update
            try:
                return self._json(200, update.check())
            except Exception as ex:
                print(f"api: update check failed: {type(ex).__name__}: {ex}", flush=True)
                return self._json(502, {"error": "update check failed"})
        if path == "/api/presets":
            return self._json(200, self.engine.config.presets)
        if path == "/api/log":
            return self._json(200, {"lines": log_tail()})
        if path == "/api/stream":
            return self._stream()
        if path.startswith("/api/"):
            return self._json(404, {"error": "not found"})
        return self._static(path)

    def _stream(self) -> None:
        """Server-sent events. Sends the revision number whenever the state
        changes, so the dashboard refetches only then instead of polling a
        large object forever. A keepalive every 15 s keeps proxies and sleeping
        laptops from quietly dropping the connection."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        seen = -1
        try:
            while not self.engine.stopping:
                revision = self.engine.wait_for_change(seen, timeout=15.0)
                payload = f"data: {revision}\n\n" if revision != seen else ": keepalive\n\n"
                seen = revision
                self.wfile.write(payload.encode())
                self.wfile.flush()
        except (BrokenPipeError, ConnectionError, OSError, ValueError):
            pass  # the tab was closed or reloaded

    def do_POST(self):
        self._dispatch("POST")

    def do_PUT(self):
        self._dispatch("PUT")

    def do_PATCH(self):
        self._dispatch("PATCH")

    def do_DELETE(self):
        self._dispatch("DELETE")

    def _dispatch(self, method: str) -> None:
        if not self._guard():
            return
        parts = urlparse(self.path).path.strip("/").split("/")
        raw = self._body()
        if raw is None:  # rejected already (bad or oversized Content-Length)
            return
        body = raw if isinstance(raw, dict) else {}
        e = self.engine
        try:
            match (method, *parts):
                case ("POST", "api", "scan"):
                    e.scan()
                    return self._json(200, {"devices": [d.to_dict() for d in e.devices]})
                case ("POST", "api", "pause"):
                    e.set_paused(bool(body.get("paused", True)))
                    return self._json(200, {"paused": e.paused})
                case ("POST", "api", "devices", device_id, "test"):
                    return self._json(200, {"touched": e.test_device(device_id, body)})
                case ("PATCH", "api", "devices", device_id):
                    return self._json(200, e.set_device_options(device_id, body))
                case ("DELETE", "api", "devices", device_id):
                    return self._json(200, {"forgotten": e.forget_device(device_id)})
                case ("POST", "api", "adapters", adapter, action):
                    return self._adapter_action(adapter, action, body)
                case ("PUT", "api", "rules"):
                    e.config.set_rules([Rule.from_dict(r) for r in raw] if isinstance(raw, list) else [])
                    e.reapply()
                    return self._json(200, [r.to_dict() for r in e.config.rules])
                case ("POST", "api", "rules"):
                    saved = e.config.upsert_rule(Rule.from_dict(body))
                    e.reapply()  # show the edit on the hardware now, not at the next event
                    return self._json(200, saved.to_dict())
                case ("POST", "api", "rules", "preview"):  # run actions now, without saving
                    from lumen.core.events import Event
                    from lumen.core.rules import Action
                    touched = [e.player.run(Action.from_dict(a), Event("test", "dashboard")) for a in (raw if isinstance(raw, list) else [])]
                    return self._json(200, {"touched": sorted({d for t in touched for d in t})})
                case ("DELETE", "api", "rules", rule_id):
                    deleted = e.config.delete_rule(rule_id)
                    e.reapply()
                    return self._json(200, {"deleted": deleted})
                case ("POST", "api", "presets"):
                    return self._json(200, e.config.upsert_preset(body))
                case ("DELETE", "api", "presets", preset_id):
                    return self._json(200, {"deleted": e.config.delete_preset(preset_id)})
                case ("DELETE", "api", "activity"):
                    e.clear_activity()
                    return self._json(200, {"cleared": True})
                case ("POST", "api", "events"):
                    return self._webhook(body)
                case ("PUT", "api", "settings"):
                    if "autostart" in body:
                        # The OS half goes first, and what it actually did is what
                        # gets stored. Saving the preference and then asking the
                        # machine leaves the config asserting a login entry that
                        # may never have been written.
                        from lumen.app import autostart
                        try:
                            autostart.set_enabled(bool(body["autostart"]))
                        except OSError as err:
                            return self._json(500, {"error": f"could not change start at login: {err}"})
                        body = {**body, "autostart": autostart.enabled()}
                    settings = e.config.update_settings(body)
                    e.apply_settings()
                    return self._json(200, settings)
                case ("POST", "api", "integrations", integ_id, verb) if verb in ("connect", "disconnect"):
                    integ = e.integration(integ_id)
                    if not integ:
                        return self._json(404, {"error": "no such integration"})
                    return self._json(200, {"message": getattr(integ, verb)(), **integ.to_dict()})
                case ("PATCH", "api", "integrations", integ_id):
                    return self._json(200, e.set_integration_options(integ_id, body))
                case ("POST", "api", "update"):
                    from lumen.app import update
                    return self._json(200, {"message": update.apply(self.engine.request_exit, e.config.settings.get("port", 6733))})
                case ("POST", "api", "onboarded"):
                    e.config.set_onboarded(True)
                    return self._json(200, {"onboarded": True})
                case ("DELETE", "api", "sessions", session_id):  # dismiss a tab that never sent SessionEnd
                    from lumen.integrations.agent_sessions import forget_session
                    return self._json(200, {"forgotten": forget_session(_session_id(session_id))})
                case ("PUT", "api", "sessions", "order"):
                    from lumen.integrations.agent_sessions import set_order
                    ids = [_session_id(s) for s in raw] if isinstance(raw, list) else []
                    moved = set_order(ids)
                    self._repaint_sessions()
                    return self._json(200, {"sessions": moved})
                case ("PATCH", "api", "sessions", session_id):
                    from lumen.integrations.agent_sessions import set_label, set_slot
                    session_id = _session_id(session_id)
                    out = {}
                    if "slot" in body:
                        out["sessions"] = set_slot(session_id, body["slot"])
                    if "label" in body:
                        out["session"] = set_label(session_id, body["label"])
                    if not out:
                        raise ValueError("nothing to change: send a slot or a label")
                    self._repaint_sessions()
                    return self._json(200, out)
                case _:
                    return self._json(404, {"error": "not found"})
        except ValueError as ex:  # bad input from the client, not our fault
            return self._json(400, {"error": str(ex)})
        except Exception as ex:  # detail stays in the log; tracebacks carry local paths
            print(f"api: {method} {self.path} failed: {type(ex).__name__}: {ex}", flush=True)
            return self._json(500, {"error": "internal error"})
        finally:
            e.bump()  # every route reachable from here changes what the dashboard shows

    def _webhook(self, body: dict) -> None:
        token = self.engine.config.settings.get("webhook_token")
        if token and not self._webhook_token_ok():
            return self._json(401, {"error": "bad token"})
        type_ = str(body.get("type") or "").strip()
        if not type_ or len(type_) > 80:
            return self._json(400, {"error": "missing event type"})
        data = body.get("data") if isinstance(body.get("data"), dict) else {}
        ev = self.engine.emit(type_, data, source=str(body.get("source") or "webhook"))
        return self._json(200, ev.to_dict())

    def _adapter_action(self, adapter: str, action: str, body: dict) -> None:
        from lumen.core.devices import adapter_modules
        mod = next((m for m in adapter_modules() if m.__name__.rsplit(".", 1)[-1] == adapter), None)
        if mod is None:
            return self._json(404, {"error": "no such adapter"})
        fn = getattr(mod, "ACTIONS", {}).get(action)
        if not fn:
            return self._json(404, {"error": "no such action"})
        message, new_options = fn(self.engine.config.device_options(adapter), body)
        if new_options:
            self.engine.config.set_device_options(adapter, new_options)
            threading.Thread(target=self.engine.scan, daemon=True).start()
        return self._json(200, {"message": message})


def make_server(engine: Engine, port: int, host: str = "127.0.0.1") -> ThreadingHTTPServer:
    handler = type("Handler", (_Handler,), {"engine": engine})
    return _Server((host, port), handler)


def serve_in_background(engine: Engine, port: int, tries: int = 10) -> ThreadingHTTPServer:
    """Serve on `port`, or the next free one. Someone else holding the port
    should not stop the daemon from coming up: the tray menu and the log say
    where it actually landed."""
    last: OSError | None = None
    for candidate in range(port, port + max(1, tries)):
        try:
            server = make_server(engine, candidate)
        except OSError as e:
            last = e
            continue
        threading.Thread(target=server.serve_forever, name="lumen-http", daemon=True).start()
        return server
    raise last or OSError("no free port")
