import json
import threading
import urllib.error
import urllib.request

import pytest

from lumen.core.config import Config
from lumen.core.devices import COLOR, NOTIFY, Device
from lumen.core.engine import Engine
from lumen.core.integrations import Integration
from lumen.server.api import make_server


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Never touch the real config/sessions directory from tests."""
    monkeypatch.setenv("LUMEN_HOME", str(tmp_path / "lumen-home"))
    yield tmp_path / "lumen-home"


class Light(Device):
    def __init__(self):
        super().__init__(id="light", name="Fake light", kind="light", vendor="Test", capabilities=frozenset({COLOR}))
        self.colors = []

    def set_color(self, rgb):
        self.colors.append(tuple(rgb))


class Toast(Device):
    def __init__(self):
        super().__init__(id="notification", name="Toast", kind="notification", capabilities=frozenset({NOTIFY}))
        self.notes = []

    def notify(self, title, message):
        self.notes.append(message)


class Src(Integration):
    id = "src"
    name = "Source"
    can_connect = True

    def connect(self):
        return "connected!"


@pytest.fixture
def server(tmp_path):
    light, toast = Light(), Toast()
    engine = Engine(Config(tmp_path / "config.json"), integrations=[Src], discover=lambda s: [light, toast])
    engine.start()
    srv = make_server(engine, 0)

    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"

    def call(method, path, body=None, token=None):
        req = urllib.request.Request(base + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json", **({"Authorization": f"Bearer {token}"} if token else {})})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read() or b"null")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    call.base = base
    yield call, engine, light, toast
    srv.shutdown()
    engine.stop()
