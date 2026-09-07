"""Philips Hue lights through the bridge's local API (v1, HTTP).

First run: the bridge is found on the local network with an SSDP probe (or set
`bridge` by hand in the device options) and shows up as an unpaired device.
Press the bridge's link button, then click *Pair* in the dashboard; the
username the bridge hands out is saved and every light becomes a device.

Nothing here phones home: the https://discovery.meethue.com lookup runs only
when the user clicks *Pair* and local discovery found nothing.

Adapter actions (POST /api/adapters/hue/<action>):
    pair   {"bridge": "<ip>"}   -> saves the username
    forget {}                   -> drops it
"""

from __future__ import annotations

import colorsys
import json
import re
import socket
import time
import urllib.request

from lumen.core.devices import BRIGHTNESS, COLOR, Device

DISCOVERY_URL = "https://discovery.meethue.com/"
_SSDP_ADDR = ("239.255.255.250", 1900)
_SSDP_PROBE = (  # Hue bridges answer the "everything" search target
    "M-SEARCH * HTTP/1.1\r\n"
    f"HOST: {_SSDP_ADDR[0]}:{_SSDP_ADDR[1]}\r\n"
    'MAN: "ssdp:discover"\r\n'
    "MX: 1\r\n"
    "ST: ssdp:all\r\n"
    "\r\n"
).encode()
_cached_bridge: str | None = None


def _http(method: str, url: str, body: dict | None = None, timeout: float = 3.0):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read() or b"null")


def _is_bridge(ip: str) -> bool:
    """The bridge's /api/config answers without a username and names itself."""
    try:
        config = _http("GET", f"http://{ip}/api/config", timeout=1.0)
        return isinstance(config, dict) and bool(config.get("bridgeid"))
    except (OSError, ValueError):
        return False


def _ssdp_bridges(timeout: float = 2.0) -> list[str]:
    """Broadcast one M-SEARCH and keep the answers that are really Hue bridges."""
    found = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(0.25)
            sock.sendto(_SSDP_PROBE, _SSDP_ADDR)
            end = time.monotonic() + timeout
            while time.monotonic() < end:
                try:
                    _, (ip, _) = sock.recvfrom(4096)
                except TimeoutError:
                    continue
                except OSError:
                    break
                if ip not in found:
                    found.append(ip)
    except OSError:
        pass  # no multicast route (VPN-only interfaces, sandboxes)
    return [ip for ip in found if _is_bridge(ip)]


def _cloud_bridge() -> str | None:
    """Philips' brokered lookup. Only the explicit Pair action may call this."""
    try:
        bridges = _http("GET", DISCOVERY_URL, timeout=3.0)
        return bridges[0]["internalipaddress"] if bridges else None
    except (OSError, ValueError, KeyError, IndexError):
        return None


def find_bridge() -> str | None:
    global _cached_bridge
    if _cached_bridge:
        return _cached_bridge
    bridges = _ssdp_bridges()
    _cached_bridge = bridges[0] if bridges else None
    return _cached_bridge


def pair(options: dict, params: dict) -> tuple[str, dict]:
    bridge = params.get("bridge") or options.get("bridge") or find_bridge() or _cloud_bridge()
    if not bridge:
        return "No Hue bridge found on this network.", {}
    try:
        reply = _http("POST", f"http://{bridge}/api", {"devicetype": f"lumen#{socket.gethostname()[:19]}"})
    except OSError as e:
        return f"Bridge at {bridge} did not answer ({e}).", {}
    if reply and "success" in reply[0]:
        return "Paired. Your Hue lights will appear after the next scan.", {"bridge": bridge, "username": reply[0]["success"]["username"]}
    return "Press the link button on the bridge, then try again within 30 seconds.", {"bridge": bridge}


def forget(options: dict, params: dict) -> tuple[str, dict]:
    return "Hue bridge forgotten.", {"bridge": options.get("bridge", ""), "username": ""}


ACTIONS = {"pair": pair, "forget": forget}


class HueLight(Device):
    def __init__(self, bridge: str, username: str, light_id: str, info: dict):
        # The bridge's uniqueid is the light's MAC plus an endpoint suffix
        # ("00:17:88:...:01-0b"); the separators carry no information and the
        # last 16 hex digits are already unique per light.
        unique = re.sub(r"[^0-9a-f]", "", str(info.get("uniqueid") or light_id).lower())[-16:]
        super().__init__(id=f"hue-{unique or light_id}", name=info.get("name", f"Hue {light_id}"),
                         kind="light", vendor="Philips Hue", capabilities=frozenset({COLOR, BRIGHTNESS}),
                         connected=bool(info.get("state", {}).get("reachable", True)),
                         details={"model": info.get("modelid", ""), "bridge": bridge, "connection": "Hue bridge"})
        self._url = f"http://{bridge}/api/{username}/lights/{light_id}"
        self.max_fps = 4  # the bridge rate-limits around 10 commands/s in total

    def set_color(self, rgb) -> None:
        if tuple(rgb) == (0, 0, 0):
            _http("PUT", self._url + "/state", {"on": False, "transitiontime": 0})
            return
        h, s, v = colorsys.rgb_to_hsv(rgb[0] / 255, rgb[1] / 255, rgb[2] / 255)
        _http("PUT", self._url + "/state", {"on": True, "hue": int(h * 65535), "sat": int(s * 254),
                                            "bri": max(1, int(v * 254)), "transitiontime": 0})

    def set_brightness(self, level: float) -> None:
        _http("PUT", self._url + "/state", {"on": level > 0, "bri": max(1, int(level * 254)), "transitiontime": 0})

    def remember(self):
        state = _http("GET", self._url).get("state", {})
        return {k: state[k] for k in ("on", "bri", "hue", "sat", "ct") if k in state}

    def restore(self, saved: object) -> None:
        if saved:
            _http("PUT", self._url + "/state", {**(saved if isinstance(saved, dict) else {}), "transitiontime": 2})


class UnpairedBridge(Device):
    def __init__(self, bridge: str):
        super().__init__(id="hue-bridge", name="Philips Hue bridge", kind="light", vendor="Philips Hue",
                         capabilities=frozenset(), connected=False,
                         details={"bridge": bridge, "setup": "hue", "hint": "Press the link button on the bridge, then Pair."})


def discover(settings: dict | None = None) -> list[Device]:
    options = (settings or {}).get("devices", {}).get("hue", {})
    bridge, username = options.get("bridge"), options.get("username")
    if not username:
        bridge = bridge or find_bridge()
        return [UnpairedBridge(bridge)] if bridge else []
    try:
        lights = _http("GET", f"http://{bridge}/api/{username}/lights")
    except (OSError, ValueError):
        return []
    if not isinstance(lights, dict):
        return [UnpairedBridge(bridge)]  # username revoked
    return [HueLight(bridge, username, lid, info) for lid, info in sorted(lights.items())]
