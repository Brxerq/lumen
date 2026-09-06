"""Hue bridge discovery stays on the LAN: the meethue.com cloud lookup only
runs from the explicit Pair action, never from a scan."""

import pytest

from lumen.devices import hue


@pytest.fixture(autouse=True)
def _no_cache(monkeypatch):
    monkeypatch.setattr(hue, "_cached_bridge", None)


def test_scan_never_calls_the_cloud(monkeypatch):
    monkeypatch.setattr(hue, "_ssdp_bridges", lambda: ["192.168.1.9"])
    monkeypatch.setattr(hue, "_cloud_bridge", lambda: pytest.fail("cloud lookup from a scan"))
    devices = hue.discover({"devices": {"hue": {}}})
    assert [d.id for d in devices] == ["hue-bridge"]
    assert devices[0].details["bridge"] == "192.168.1.9"

    monkeypatch.setattr(hue, "_ssdp_bridges", lambda: [])
    monkeypatch.setattr(hue, "_cached_bridge", None)
    assert hue.discover({"devices": {"hue": {}}}) == []  # nothing local: nothing found, still no cloud


def test_find_bridge_caches_the_local_answer(monkeypatch):
    probes = []
    monkeypatch.setattr(hue, "_ssdp_bridges", lambda: probes.append(1) or ["10.0.0.2"])
    assert hue.find_bridge() == "10.0.0.2"
    assert hue.find_bridge() == "10.0.0.2"
    assert probes == [1]  # second call served from cache


def test_pair_falls_back_to_the_cloud_only_when_explicit(monkeypatch):
    monkeypatch.setattr(hue, "find_bridge", lambda: None)
    monkeypatch.setattr(hue, "_cloud_bridge", lambda: "192.168.1.9")
    answered = []
    monkeypatch.setattr(hue, "_http", lambda m, url, body=None, timeout=3.0:
                        answered.append(url) or [{"success": {"username": "abc"}}])
    message, options = hue.pair({}, {})
    assert options == {"bridge": "192.168.1.9", "username": "abc"}
    assert answered == ["http://192.168.1.9/api"]

    # A configured or passed bridge never consults discovery at all.
    monkeypatch.setattr(hue, "find_bridge", lambda: pytest.fail("not needed"))
    monkeypatch.setattr(hue, "_cloud_bridge", lambda: pytest.fail("not needed"))
    answered.clear()
    _, options = hue.pair({"bridge": "10.0.0.2"}, {})
    assert options["bridge"] == "10.0.0.2"


def test_discover_with_credentials_asks_the_bridge(monkeypatch):
    monkeypatch.setattr(hue, "_http", lambda m, url, body=None, timeout=3.0: {
        "1": {"uniqueid": "00:17:88:01:00:00:00:01-0b", "name": "Desk", "state": {"reachable": True}},
    })
    devices = hue.discover({"devices": {"hue": {"bridge": "10.0.0.2", "username": "abc"}}})
    assert [d.id for d in devices] == ["hue-178801000000010b"]
    assert devices[0].name == "Desk"
