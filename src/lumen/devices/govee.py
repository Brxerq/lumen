"""Govee lights over the LAN API (no cloud, no API key).

Enable "LAN Control" for the light in the Govee Home app first. Discovery is
a UDP multicast scan; control is a UDP JSON message per command.

    scan     -> 239.255.255.250:4001   {"msg":{"cmd":"scan","data":{"account_topic":"reserve"}}}
    replies  <- 0.0.0.0:4002           {"msg":{"cmd":"scan","data":{"ip":..,"device":..,"sku":..}}}
    control  -> <ip>:4003              turn / brightness / colorwb

Reference: https://app-h5.govee.com/user-manual/wlan-guide
"""

from __future__ import annotations

import json
import socket
import time

from lumen.core.devices import BRIGHTNESS, COLOR, Device

MCAST, SCAN_PORT, REPLY_PORT, CONTROL_PORT = "239.255.255.250", 4001, 4002, 4003
SCAN_WAIT_S = 1.5


def _send(ip: str, msg: dict) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.sendto(json.dumps({"msg": msg}).encode(), (ip, CONTROL_PORT))


class GoveeLight(Device):
    def __init__(self, ip: str, mac: str, sku: str):
        kind = "strip" if sku.upper().startswith(("H61", "H70", "H619", "H6159")) else "light"
        super().__init__(id=f"govee-{mac.replace(':', '').lower()}", name=f"Govee {sku}", kind=kind, vendor="Govee",
                         capabilities=frozenset({COLOR, BRIGHTNESS}),
                         details={"ip": ip, "sku": sku, "protocol": "Govee LAN", "connection": "Wi-Fi"})
        self.ip = ip
        self.max_fps = 10

    def set_color(self, rgb) -> None:
        if tuple(rgb) == (0, 0, 0):
            _send(self.ip, {"cmd": "turn", "data": {"value": 0}})
            return
        _send(self.ip, {"cmd": "turn", "data": {"value": 1}})
        _send(self.ip, {"cmd": "colorwb", "data": {"color": {"r": rgb[0], "g": rgb[1], "b": rgb[2]}, "colorTemInKelvin": 0}})

    def set_brightness(self, level: float) -> None:
        _send(self.ip, {"cmd": "brightness", "data": {"value": int(round(level * 100))}})


def scan(wait_s: float = SCAN_WAIT_S) -> list[dict]:
    """[{ip, device, sku}] for every light that answers the multicast scan."""
    try:
        listener = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("", REPLY_PORT))
        listener.settimeout(0.3)
    except OSError:
        return []  # port taken by another Govee client
    found: dict[str, dict] = {}
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
            s.sendto(json.dumps({"msg": {"cmd": "scan", "data": {"account_topic": "reserve"}}}).encode(), (MCAST, SCAN_PORT))
        deadline = time.monotonic() + wait_s
        while time.monotonic() < deadline:
            try:
                raw, _ = listener.recvfrom(4096)
            except TimeoutError:
                continue
            except OSError:
                break
            try:
                data = json.loads(raw)["msg"]["data"]
                if data.get("ip") and data.get("device"):
                    found[data["device"]] = {"ip": data["ip"], "device": data["device"], "sku": data.get("sku", "light")}
            except (ValueError, KeyError, TypeError):
                continue
    finally:
        listener.close()
    return list(found.values())


def discover(settings: dict | None = None) -> list[Device]:
    if (settings or {}).get("devices", {}).get("govee", {}).get("enabled", True) is False:
        return []
    return [GoveeLight(d["ip"], d["device"], d["sku"]) for d in scan()]
