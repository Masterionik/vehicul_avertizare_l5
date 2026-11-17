"""Scripted remote vehicles V2 (Heltec e-ink) and V3 (Seeed XIAO).

The two extra Meshtastic boards act as other vehicles: they send signed alert packets to the Pi so the
whole chain (radio -> RTE -> ALR -> buzzer/LED -> dashboard) can be shown without extra vehicles.

* Real board: set ``SIM_V2_PORT`` / ``SIM_V2_HOST`` (and ``SIM_V3_*``) to a serial port or TCP host;
  the alert then travels over real LoRa to the Pi's own modem.
* No board (simulation): the packet is produced by the same sdc code and handed directly to the
  Pi's simulated receive queue. It never touches a radio, and the result says ``simulated: true``.
* Zigbee scripts only work while the Pi's Zigbee link is simulated (no XBee for the extra vehicles).
"""

from __future__ import annotations

import os
import threading

from rte.sim_support import SIM_tGetLinks
from sdc import MeshtasticLink, ZigbeeLink, node_id_from_name

# script id -> (kind, level, distance_cm, description)
SCRIPTS = {
    "caution": (1, 1, 120, "proximity caution at 120 cm"),
    "warning": (1, 2, 40, "proximity warning at 40 cm"),
    "danger": (1, 3, 20, "proximity danger at 20 cm"),
    "impact": (2, 3, 0, "impact"),
    "clear": (1, 0, 300, "all clear"),
}
VEHICLES = {
    "V2": {"board": "Heltec e-ink"},
    "V3": {"board": "Seeed XIAO"},
}
RADIOS = ("mesh", "zigbee")


class RemoteVehicle:
    def __init__(self, name):
        self.name = name
        self.board = VEHICLES[name]["board"]
        raw = os.environ.get("SIM_%s_NODE_ID" % name, "").strip()
        try:
            self.node_id = int(raw, 0) if raw else node_id_from_name("vehicle-" + name)
        except ValueError:
            self.node_id = node_id_from_name("vehicle-" + name)
        self._lock = threading.Lock()
        self._mesh = None
        self._zigbee = None

    def _mesh_link(self):
        if self._mesh is None:
            self._mesh = MeshtasticLink(port=os.environ.get("SIM_%s_PORT" % self.name) or None,
                                        host=os.environ.get("SIM_%s_HOST" % self.name) or None,
                                        name=self.name, node_id=self.node_id)
        if not self._mesh.is_connected():
            self._mesh.connect()
        return self._mesh

    def _zigbee_link(self):
        if self._zigbee is None:
            self._zigbee = ZigbeeLink(simulated=True, name=self.name + "-zigbee", node_id=self.node_id)
        if not self._zigbee.is_connected():
            self._zigbee.connect()
        return self._zigbee

    def info(self):
        link = self._mesh
        simulated = link.simulated if link is not None else not (
            os.environ.get("SIM_%s_PORT" % self.name) or os.environ.get("SIM_%s_HOST" % self.name))
        return {"name": self.name, "board": self.board, "node_id": self.node_id,
                "simulated": bool(simulated),
                "connected": bool(link is not None and link.is_connected())}

    def send(self, script, radio="mesh"):
        """Send the scripted alert. Returns a result dict (never raises for bad input)."""
        result = {"vehicle": self.name, "script": script, "radio": radio, "ok": False,
                  "sent_bytes": 0, "delivered": False, "simulated": True, "message": ""}
        if script not in SCRIPTS:
            result["message"] = "unknown script"
            return result
        if radio not in RADIOS:
            result["message"] = "unknown radio"
            return result
        kind, level, distance, text = SCRIPTS[script]
        with self._lock:
            link = self._mesh_link() if radio == "mesh" else self._zigbee_link()
            result["simulated"] = bool(link.simulated)
            sent = link.send_alert(kind, level, distance)
            if sent <= 0:
                error = link.info().get("error")
                result["message"] = "%s could not send: %s" % (self.name, error or "link not connected")
                return result
            result["sent_bytes"] = sent
            result["ok"] = True
            if not link.simulated:
                result["delivered"] = True       # on the air; the Pi's modem receives it over LoRa
                result["message"] = "%s sent %s over %s" % (self.name, text, radio)
                return result
            modem, zigbee = SIM_tGetLinks()
            target = modem if radio == "mesh" else zigbee
            if target is None or not target.simulated:
                result["message"] = ("%s is simulated and cannot reach a real %s radio on the Pi"
                                     % (self.name, radio))
                return result
            raw = bytes(link.transport.sent_packets[-1])
            target.transport.inject_rx(raw)
            result["delivered"] = True
            result["message"] = "%s (simulated) sent %s over %s" % (self.name, text, radio)
            return result


_vehicles = {}
_vehicles_lock = threading.Lock()


def get_vehicle(name):
    if name not in VEHICLES:
        return None
    with _vehicles_lock:
        if name not in _vehicles:
            _vehicles[name] = RemoteVehicle(name)
        return _vehicles[name]


def list_vehicles():
    return [get_vehicle(name).info() for name in VEHICLES]


def send_scripted_alert(vehicle, script, radio="mesh"):
    """Make remote vehicle ``vehicle`` (V2 or V3) send ``script`` over ``radio`` ("mesh" or "zigbee")."""
    v = get_vehicle(vehicle)
    if v is None:
        return {"vehicle": vehicle, "script": script, "radio": radio, "ok": False,
                "sent_bytes": 0, "delivered": False, "simulated": True, "message": "unknown vehicle"}
    return v.send(script, radio)


def reset_vehicles():
    """Disconnect and forget all vehicles (used by tests and on shutdown)."""
    with _vehicles_lock:
        for v in _vehicles.values():
            for link in (v._mesh, v._zigbee):
                if link is not None:
                    try:
                        link.disconnect()
                    except Exception:
                        pass
        _vehicles.clear()
