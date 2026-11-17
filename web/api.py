"""API logic of the dashboard, independent of Django: every function returns ``(http_status, dict)``.

Views (``web/views.py``) only parse the request and call these functions. Nothing here touches the
hardware directly: values come from RTE signals and the runtime status, actuation is a request that
the single runtime thread executes (``app.node``), and simulation hooks go through ``rte.sim_support``.
"""

from __future__ import annotations

import time

from app import node
from rte import (RTE_u8GetMeshStatus, RTE_u8GetZigbeeStatus, RTE_u8ReadAlertLevel,
                 RTE_u8ReadRemoteAlertLevel, RTE_u16ReadDistanceCm, RTE_u16SendMeshText,
                 RTE_u16SendZigbeeAlert)
from rte.sim_support import SIM_bInjectVibrationEdges, SIM_bSensorsSimulated, SIM_bSetDistanceCm
from sim import devices, vehicles

PULSE_DEFAULT_MS = 500
PULSE_MAX_MS = 2000
EVENTS_IN_STATE = 30
MESH_TEST_TEXT = "TEST-MESH Pi proximity-warning"


def parse_int(value):
    """Real int, or a string of digits (form posts); anything else (bool, float, None, text) -> None."""
    if type(value) is int:
        return value
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    return None


def parse_confirm(value):
    return value is True or (isinstance(value, str) and value.strip().lower() in ("true", "1", "yes"))


def _err(status, message):
    return status, {"ok": False, "error": message}


# ----------------------------------------------------------------------------- state
def _link_state(info):
    return {"connected": bool(info.get("connected")), "simulated": info.get("simulated"),
            "node_id": info.get("node_id"), "stats": info.get("stats", {}), "error": info.get("error")}


def state():
    """Everything the dashboard shows, in one JSON document (polled every 500 ms)."""
    status = node.NODE_tGetStatus()
    radios = devices.radio_info()
    mesh = _link_state(radios["meshtastic"])
    zigbee = _link_state(radios["zigbee"])
    mesh["connected"] = bool(RTE_u8GetMeshStatus())
    zigbee["connected"] = bool(RTE_u8GetZigbeeStatus())
    sensors_simulated = bool(SIM_bSensorsSimulated())
    remote = status["last_remote_alert"]
    if remote:
        remote["age_s"] = round(max(0, int(time.monotonic() * 1000) - remote.pop("received_ms")) / 1000, 1)
    events = node.NODE_lGetEvents()[-EVENTS_IN_STATE:]
    return {
        "time": time.time(),
        "running": status["running"],
        "distance_cm": status["distance_cm"],
        "sensor_fault": status["sensor_fault"],
        "level": status["level"],
        "buzzer": status["buzzer"],
        "led": status["led"],
        "impact": status["impact"],
        "remote_level": RTE_u8ReadRemoteAlertLevel(),
        "last_remote_alert": remote,
        "mesh": mesh,
        "zigbee": zigbee,
        "simulation": {
            "sensors": sensors_simulated,
            "meshtastic": bool(mesh["simulated"]),
            "zigbee": bool(zigbee["simulated"]),
            "any": sensors_simulated or bool(mesh["simulated"]) or bool(zigbee["simulated"]),
        },
        "counters": status["counters"],
        "vibration_edges_total": status["vibration_edges_total"],
        "impacts_total": status["impacts_total"],
        "report": status["report"],
        "events": events,
    }


# ------------------------------------------------------------------------- actuators
def pulse(output, duration_ms=None):
    """Buzzer or LED test pulse (default 500 ms, at most 2 s, then off automatically)."""
    if output not in ("buzzer", "led"):
        return _err(400, "output must be buzzer or led")
    duration = PULSE_DEFAULT_MS if duration_ms is None else parse_int(duration_ms)
    if duration is None or not 1 <= duration <= PULSE_MAX_MS:
        return _err(400, "duration_ms must be 1-%d" % PULSE_MAX_MS)
    if not node.NODE_bIsRunning():
        return _err(503, "runtime is not running")
    if node.NODE_bAlertActive():
        return _err(409, "refused: a real alert is active")
    if not node.NODE_bRequestPulse(output, duration):
        return _err(409, "refused: a real alert is active")
    return 200, {"ok": True, "output": output, "duration_ms": duration}


def outputs_off():
    """Emergency stop: cancel test pulses and force buzzer and LED off."""
    node.NODE_vClearPulses()
    return 200, {"ok": True}


def distance_read():
    """Latest ultrasonic measurement taken by the runtime thread."""
    if not node.NODE_bIsRunning():
        return _err(503, "runtime is not running")
    status = node.NODE_tGetStatus()
    return 200, {"ok": True, "distance_cm": status["distance_cm"], "valid": status["distance_cm"] is not None,
                 "sensor_fault": status["sensor_fault"]}


def mesh_test():
    sent = RTE_u16SendMeshText(MESH_TEST_TEXT)
    if sent <= 0:
        return _err(502, "Meshtastic link is not connected")
    return 200, {"ok": True, "sent_bytes": sent, "simulated": bool(devices.radio_info()["meshtastic"].get("simulated"))}


# ------------------------------------------------------------------------ simulation
def sim_distance(value):
    distance = parse_int(value)
    if distance is None or not 0 <= distance <= 1000:
        return _err(400, "distance_cm must be an integer 0-1000 (0 = no echo)")
    if not SIM_bSetDistanceCm(distance):
        return _err(403, "the distance sensor is real hardware, not simulated")
    return 200, {"ok": True, "distance_cm": distance}


def sim_vibration(count):
    edges = parse_int(count)
    if edges is None or not 1 <= edges <= 50:
        return _err(400, "count must be an integer 1-50")
    if not SIM_bInjectVibrationEdges(edges):
        return _err(403, "the vibration input is real hardware, not simulated")
    return 200, {"ok": True, "edges": edges}


def sim_remote_alert(vehicle, script, radio="mesh"):
    result = vehicles.send_scripted_alert(str(vehicle), str(script), str(radio))
    if not result["ok"]:
        return 400 if result["message"].startswith("unknown") else 502, {"error": result["message"], **result}
    if not result["delivered"]:
        return 409, {"error": result["message"], **result}
    return 200, result


# ----------------------------------------------------------------------------- tests
def _wait(predicate, timeout_s=1.5):
    end = time.monotonic() + timeout_s
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


def _result(name, status, detail):
    return {"name": name, "status": status, "detail": detail}


def _t_buzzer_led():
    if not node.NODE_bIsRunning():
        return _result("buzzer_led", "FAIL", "runtime is not running")
    if node.NODE_bAlertActive():
        return _result("buzzer_led", "SKIP", "a real alert is active")
    if not (node.NODE_bRequestPulse("buzzer", 300) and node.NODE_bRequestPulse("led", 300)):
        return _result("buzzer_led", "FAIL", "pulse request refused")
    ok = _wait(lambda: node.NODE_tGetStatus()["buzzer"] == 1 and node.NODE_tGetStatus()["led"] == 1)
    return _result("buzzer_led", "PASS" if ok else "FAIL",
                   "buzzer and LED switched on for 300 ms" if ok else "outputs did not switch on")


def _t_ultrasonic():
    if not node.NODE_bIsRunning():
        return _result("ultrasonic", "FAIL", "runtime is not running")
    ok = _wait(lambda: node.NODE_tGetStatus()["distance_cm"] is not None)
    distance = node.NODE_tGetStatus()["distance_cm"]
    if ok and 2 <= distance <= 400:
        return _result("ultrasonic", "PASS", "valid reading: %d cm" % distance)
    return _result("ultrasonic", "FAIL", "no valid echo (check wiring, ECHO divider and target distance)")


def _t_vibration():
    if not node.NODE_bIsRunning():
        return _result("vibration", "FAIL", "runtime is not running")
    before = node.NODE_tGetStatus()["vibration_edges_total"]
    if SIM_bSensorsSimulated():
        SIM_bInjectVibrationEdges(1)
        ok = _wait(lambda: node.NODE_tGetStatus()["vibration_edges_total"] > before)
        return _result("vibration", "PASS" if ok else "FAIL",
                       "1 simulated edge counted" if ok else "injected edge was not counted")
    ok = _wait(lambda: node.NODE_tGetStatus()["vibration_edges_total"] > before, 3.0)
    return _result("vibration", "PASS" if ok else "FAIL",
                   "edge counted" if ok else "no edge within 3 s: tap the sensor and run the test again")


def _t_mesh():
    if not RTE_u8GetMeshStatus():
        return _result("meshtastic", "FAIL", "link not connected")
    sent = RTE_u16SendMeshText(MESH_TEST_TEXT)
    simulated = devices.radio_info()["meshtastic"].get("simulated")
    suffix = " (SIMULATED)" if simulated else ""
    return _result("meshtastic", "PASS" if sent > 0 else "FAIL", "%d bytes sent%s" % (max(sent, 0), suffix))


def _t_zigbee():
    if not RTE_u8GetZigbeeStatus():
        return _result("zigbee", "FAIL", "link not connected")
    sent = RTE_u16SendZigbeeAlert(1, 0, 300)
    simulated = devices.radio_info()["zigbee"].get("simulated")
    suffix = " (SIMULATED)" if simulated else ""
    return _result("zigbee", "PASS" if sent > 0 else "FAIL", "%d bytes sent%s" % (max(sent, 0), suffix))


def _t_rte():
    problems = []
    if not 0 <= RTE_u8ReadAlertLevel() <= 3:
        problems.append("alert level out of range")
    if not 0 <= RTE_u8ReadRemoteAlertLevel() <= 3:
        problems.append("remote level out of range")
    distance = RTE_u16ReadDistanceCm()
    if not (distance == 0xFFFF or 0 <= distance <= 400):
        problems.append("distance signal out of range")
    if not node.NODE_bIsRunning():
        problems.append("runtime is not running")
    return _result("rte", "FAIL" if problems else "PASS", "; ".join(problems) or "signals in range, runtime running")


TESTS = {"buzzer_led": _t_buzzer_led, "ultrasonic": _t_ultrasonic, "vibration": _t_vibration,
         "meshtastic": _t_mesh, "zigbee": _t_zigbee, "rte": _t_rte}


def tests_list():
    return 200, {"tests": list(TESTS)}


def tests_run(name):
    """Run one component test, or all of them with ``name == "all"``."""
    if name == "all":
        return 200, {"results": [fn() for fn in TESTS.values()]}
    if name not in TESTS:
        return _err(404, "unknown test")
    return 200, {"results": [TESTS[name]()]}


# --------------------------------------------------------------------------- devices
def devices_state():
    return 200, {"radios": devices.radio_info(), "vehicles": vehicles.list_vehicles(),
                 "devices": devices.list_devices(), "log": devices.device_log()[-50:]}


def devices_scan():
    return 200, devices.scan()


def devices_add_tcp(host, port=None):
    parsed = 4403 if port in (None, "") else parse_int(port)
    result = devices.add_tcp(host, parsed if parsed is not None else -1)
    return (200 if result["ok"] else 400), result


def device_action(device_id, action, confirm=False):
    if action == "connect":
        result = devices.connect(device_id)
    elif action == "test":
        result = devices.send_test(device_id)
    elif action == "disconnect":
        result = devices.disconnect(device_id, confirm=confirm)
    elif action == "remove":
        result = devices.remove(device_id, confirm=confirm)
    else:
        return _err(404, "unknown action")
    if result["ok"]:
        return 200, result
    message = result.get("message", "")
    status = 404 if message == "device not found" else 400 if message == "confirmation required" else 409
    return status, {"error": message, **result}
