"""Device manager: built-in radios, saved devices (list, scan, connect, test message) and a device log.

Wraps ``sdc.DeviceRegistry``. Connecting a saved device never takes over the port that the Pi's own
modem or XBee already uses.
"""

from __future__ import annotations

import threading
import time
from collections import deque

from rte.sim_support import SIM_tGetLinks
from sdc.registry import DeviceRegistry

_registry = None
_lock = threading.RLock()
_log = deque(maxlen=200)


def set_registry(registry):
    """Use another registry (tests use a temporary file)."""
    global _registry
    with _lock:
        _registry = registry


def _reg():
    global _registry
    with _lock:
        if _registry is None:
            _registry = DeviceRegistry()
        return _registry


def _log_event(action, device_id, ok, message):
    _log.append({"time": time.time(), "action": action, "device": device_id, "ok": bool(ok),
                 "message": str(message)})


def device_log():
    """Recent device actions, oldest first."""
    return list(_log)


def radio_info():
    """Info of the Pi's own radios: ``{"meshtastic": {...}, "zigbee": {...}}`` (``connected`` False before init)."""
    modem, zigbee = SIM_tGetLinks()
    out = {}
    for key, link in (("meshtastic", modem), ("zigbee", zigbee)):
        if link is None:
            out[key] = {"connected": False, "simulated": None, "stats": {}, "error": "not initialized"}
        else:
            out[key] = link.info()
    return out


def _own_ports():
    ports = set()
    for info in radio_info().values():
        port = info.get("port")
        if port:
            ports.add(str(port))
    return ports


def list_devices():
    """Saved devices; each has ``in_use_by_pi`` True when it is the Pi's own modem / XBee port."""
    own = _own_ports()
    devices = []
    for device in _reg().list_devices():
        item = dict(device)
        item["in_use_by_pi"] = str(device.get("port") or device.get("address") or "") in own
        devices.append(item)
    return devices


def scan():
    """Scan USB serial devices (only plausible ones, see sdc.registry)."""
    devices = _reg().scan()
    _log_event("scan", "-", True, "%d device(s) found" % len(devices))
    return {"ok": True, "count": len(devices), "devices": list_devices()}


def add_tcp(host, port=4403, label=""):
    """Save a Meshtastic board reachable over WiFi/TCP (default port 4403)."""
    host = str(host or "").strip()
    if not host or len(host) > 100 or any(c.isspace() for c in host) or "/" in host:
        return {"ok": False, "message": "invalid host"}
    if type(port) is not int or not 1 <= port <= 65535:
        return {"ok": False, "message": "invalid port"}
    device = _reg().add_device({
        "id": "tcp-%s-%d" % (host.replace(":", "-"), port), "label": label or host, "type": "meshtastic",
        "transport": "tcp", "address": "%s:%d" % (host, port), "port": port, "role": "Meshtastic (LoRa)",
        "state": "disconnected"})
    _log_event("add", device["id"], True, "TCP device %s:%d saved" % (host, port))
    return {"ok": True, "device": device}


def _guard(device_id, action):
    device = _reg().get_device(device_id)
    if device is None:
        _log_event(action, device_id, False, "device not found")
        return None, {"ok": False, "message": "device not found"}
    if str(device.get("port") or device.get("address") or "") in _own_ports():
        message = "this port is used by the Pi's own radio"
        _log_event(action, device_id, False, message)
        return None, {"ok": False, "message": message}
    return device, None


def connect(device_id):
    device, error = _guard(device_id, "connect")
    if error:
        return error
    out = _reg().connect_device(device_id)
    result = out["result"]
    _log_event("connect", device_id, result["ok"], result["message"])
    return {"ok": result["ok"], "message": result["message"], "device": out["device"]}


def disconnect(device_id, confirm=False):
    """Destructive: needs ``confirm=True``."""
    if confirm is not True:
        return {"ok": False, "message": "confirmation required"}
    device, error = _guard(device_id, "disconnect")
    if error:
        return error
    out = _reg().disconnect_device(device_id)
    _log_event("disconnect", device_id, True, out["result"]["message"])
    return {"ok": True, "message": out["result"]["message"], "device": out["device"]}


def remove(device_id, confirm=False):
    """Destructive: needs ``confirm=True``."""
    if confirm is not True:
        return {"ok": False, "message": "confirmation required"}
    ok = _reg().remove_device(device_id)
    _log_event("remove", device_id, ok, "device removed" if ok else "device not found")
    return {"ok": ok, "message": "device removed" if ok else "device not found"}


def send_test(device_id):
    device, error = _guard(device_id, "send-test")
    if error:
        return error
    out = _reg().send_test(device_id)
    result = out["result"]
    _log_event("send-test", device_id, result["ok"], result["message"])
    return {"ok": result["ok"], "message": result["message"], "preview": out.get("preview")}
