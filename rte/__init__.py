"""RTE layer (lab L5): the only interface the application uses to reach the BSW and to exchange
signals between application modules.

Conventions (lab L5):
* ``v`` = void, ``u8`` / ``u16`` = unsigned 8 / 16 bit, ``b`` = bool, ``t`` = tuple/dict.
* Binary parameters and return values are 0 or 1. Only real ints are accepted (bool, float, None,
  str, 2, -1 are rejected).
* Invalid parameters are IGNORED: the output or the signal does not change, no exception.
* Every function is documented in docs/rte_api.md (table per function); the docstrings repeat the
  facts in short form.

The RTE imports only ioc, sdc and sched. Hardware access is meant to be serialized by ONE runtime
thread; the signal storage and the radio links are lock protected so that other threads (web views)
can still read signals safely.
"""

from __future__ import annotations

import os
import socket
import threading

from ioc import (IOC_CH_BUZZER, IOC_CH_LED, IOC_DISTANCE_INVALID, IOC_u8GetDistanceStatus,
                 IOC_u8GetVibrationState, IOC_u16GetDistanceCm, IOC_u16GetVibrationEdges,
                 IOC_vDeinit, IOC_vInit, IOC_vSetOutputPort)
from sched import (SCHED_bTick1s, SCHED_bTick60s, SCHED_bTick100ms, SCHED_vStart, SCHED_vStop)
from sdc import MeshtasticLink, ZigbeeLink, node_id_from_name

RTE_DISTANCE_INVALID = IOC_DISTANCE_INVALID      # 0xFFFF: no echo / out of range
RTE_KIND_PROXIMITY = 1
RTE_KIND_IMPACT = 2
RTE_LEVEL_MAX = 3
RTE_MESH_TEXT_MAX_BYTES = 200

_U16_MAX = 0xFFFF
_NODE_ID_ENV = "MESH_NODE_ID"

_state_lock = threading.RLock()
_initialized = False
_mesh: MeshtasticLink | None = None
_zigbee: ZigbeeLink | None = None

_sig_lock = threading.Lock()
_SIGNAL_DEFAULTS = {"distance_cm": RTE_DISTANCE_INVALID, "alert_level": 0,
                    "remote_alert_level": 0, "impact": 0}
_signals = dict(_SIGNAL_DEFAULTS)


# ---------------------------------------------------------------- argument checks (private)
def _is_int(value):
    return type(value) is int


def _is_binary(value):
    return _is_int(value) and value in (0, 1)


def _in_range(value, low, high):
    return _is_int(value) and low <= value <= high


def _node_id():
    raw = os.environ.get(_NODE_ID_ENV, "").strip()
    if raw:
        try:
            value = int(raw, 0)
            if 0 <= value <= 0xFFFFFFFF:
                return value
        except ValueError:
            pass
    return node_id_from_name("node-" + socket.gethostname())


# ------------------------------------------------------------------------------ lifecycle
def RTE_vInit():
    """Initialize IOC, SCHED and both radio links; buzzer and LED start off. Calling it twice is safe."""
    global _initialized, _mesh, _zigbee
    with _state_lock:
        if _initialized:
            return
        IOC_vInit()
        IOC_vSetOutputPort(IOC_CH_BUZZER, 0)
        IOC_vSetOutputPort(IOC_CH_LED, 0)
        SCHED_vStart()
        node = _node_id()
        _mesh = MeshtasticLink(modem=True, name="modem", node_id=node)
        _zigbee = ZigbeeLink(name="zigbee", node_id=node)
        _mesh.connect()
        _zigbee.connect()
        _signals.update(_SIGNAL_DEFAULTS)
        _initialized = True


def RTE_vDeinit():
    """Buzzer and LED off, close both radio links, stop the timers, reset all signals."""
    global _initialized, _mesh, _zigbee
    with _state_lock:
        try:
            IOC_vSetOutputPort(IOC_CH_BUZZER, 0)
            IOC_vSetOutputPort(IOC_CH_LED, 0)
        finally:
            links, _mesh, _zigbee = (_mesh, _zigbee), None, None
            try:
                for link in links:
                    if link is not None:
                        try:
                            link.disconnect()
                        except Exception:
                            pass
                SCHED_vStop()
            finally:
                try:
                    IOC_vDeinit()          # also forces both outputs off again
                finally:
                    with _sig_lock:
                        _signals.update(_SIGNAL_DEFAULTS)
                    _initialized = False


# ---------------------------------------------------------------------------- BSW outputs
def RTE_vSetBuzzer(u8Value):
    """Buzzer (GPIO17) on/off. 0 = off, 1 = on; anything else is ignored."""
    if _is_binary(u8Value):
        IOC_vSetOutputPort(IOC_CH_BUZZER, u8Value)


def RTE_vSetLed(u8Value):
    """LED (GPIO27) on/off. 0 = off, 1 = on; anything else is ignored."""
    if _is_binary(u8Value):
        IOC_vSetOutputPort(IOC_CH_LED, u8Value)


# ----------------------------------------------------------------------------- BSW inputs
def RTE_u16GetDistanceCm():
    """Measure once: distance in cm (2-400) or RTE_DISTANCE_INVALID (0xFFFF)."""
    return IOC_u16GetDistanceCm()


def RTE_u8GetDistanceSensorStatus():
    """1 if the last distance measurement was valid, else 0."""
    return IOC_u8GetDistanceStatus()


def RTE_u16GetVibrationEdges():
    """Vibration edges counted since the previous call (the count is consumed)."""
    return IOC_u16GetVibrationEdges()


def RTE_u8GetVibrationState():
    """Current raw level of the vibration inputs, 0 or 1."""
    return IOC_u8GetVibrationState()


# ------------------------------------------------------------------------------- timing
def RTE_u8GetTick100ms():
    """1 once per elapsed 100 ms period (consumes the event), else 0."""
    return 1 if SCHED_bTick100ms() else 0


def RTE_u8GetTick1s():
    """1 once per elapsed 1 s period (consumes the event), else 0."""
    return 1 if SCHED_bTick1s() else 0


def RTE_u8GetTick60s():
    """1 once per elapsed 60 s period (consumes the event), else 0."""
    return 1 if SCHED_bTick60s() else 0


# --------------------------------------------------------------------------- radio links
def _link_status(link):
    return 1 if link is not None and link.is_connected() else 0


def _send_alert(link, u8Kind, u8Level, u16DistanceCm):
    if link is None:
        return 0
    if not (_in_range(u8Kind, RTE_KIND_PROXIMITY, RTE_KIND_IMPACT)
            and _in_range(u8Level, 0, RTE_LEVEL_MAX)
            and _in_range(u16DistanceCm, 0, _U16_MAX)):
        return 0
    return int(link.send_alert(u8Kind, u8Level, u16DistanceCm))


def RTE_u8GetMeshStatus():
    """1 if the LoRa Meshtastic modem link is connected, else 0."""
    return _link_status(_mesh)


def RTE_u16SendMeshAlert(u8Kind, u8Level, u16DistanceCm):
    """Send a signed alert over LoRa Meshtastic. Returns bytes sent; 0 on invalid arguments or no link."""
    return _send_alert(_mesh, u8Kind, u8Level, u16DistanceCm)


def RTE_u16SendMeshText(text):
    """Send a short text (device test) over LoRa Meshtastic. Returns bytes sent; 0 if invalid or no link."""
    link = _mesh
    if link is None or not isinstance(text, str) or not text:
        return 0
    if len(text.encode("utf-8")) > RTE_MESH_TEXT_MAX_BYTES:
        return 0
    return int(link.send_text("^all", text))


def RTE_tGetMeshAlert():
    """Oldest valid received Meshtastic alert {node, kind, level, distance_cm, seq}, or None.

    Packets with a bad signature or a replayed sequence number are dropped.
    """
    link = _mesh
    return link.recv_alert() if link is not None else None


def RTE_u8GetZigbeeStatus():
    """1 if the Zigbee link is connected (simulated link counts when no XBee is present), else 0."""
    return _link_status(_zigbee)


def RTE_u16SendZigbeeAlert(u8Kind, u8Level, u16DistanceCm):
    """Send a signed alert over Zigbee. Returns bytes sent; 0 on invalid arguments or no link."""
    return _send_alert(_zigbee, u8Kind, u8Level, u16DistanceCm)


def RTE_tGetZigbeeAlert():
    """Oldest valid received Zigbee alert {node, kind, level, distance_cm, seq}, or None."""
    link = _zigbee
    return link.recv_alert() if link is not None else None


# ------------------------------------------------------- signals (virtual functional bus)
def _write_signal(name, value):
    with _sig_lock:
        _signals[name] = value


def _read_signal(name):
    with _sig_lock:
        return _signals[name]


def RTE_vWriteDistanceCm(u16Value):
    """Store the latest filtered distance in cm (0-65535, 0xFFFF = invalid); invalid values are ignored."""
    if _in_range(u16Value, 0, _U16_MAX):
        _write_signal("distance_cm", u16Value)


def RTE_u16ReadDistanceCm():
    """Latest stored distance in cm (RTE_DISTANCE_INVALID until written)."""
    return _read_signal("distance_cm")


def RTE_vWriteAlertLevel(u8Value):
    """Store the local alert level 0-3; invalid values are ignored."""
    if _in_range(u8Value, 0, RTE_LEVEL_MAX):
        _write_signal("alert_level", u8Value)


def RTE_u8ReadAlertLevel():
    """Local alert level 0-3."""
    return _read_signal("alert_level")


def RTE_vWriteRemoteAlertLevel(u8Value):
    """Store the alert level received from another node, 0-3; invalid values are ignored."""
    if _in_range(u8Value, 0, RTE_LEVEL_MAX):
        _write_signal("remote_alert_level", u8Value)


def RTE_u8ReadRemoteAlertLevel():
    """Alert level received from another node, 0-3."""
    return _read_signal("remote_alert_level")


def RTE_vWriteImpact(u8Value):
    """Store the impact flag, 0 or 1; invalid values are ignored."""
    if _is_binary(u8Value):
        _write_signal("impact", u8Value)


def RTE_u8ReadImpact():
    """Impact flag, 0 or 1."""
    return _read_signal("impact")
