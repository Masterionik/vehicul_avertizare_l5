"""Simulation support for the web and sim layers (NOT part of the L5 RTE function set).

``web`` and ``sim`` may only import ``rte``, so the few simulation hooks they need are exposed here.
They never act on real hardware: every setter is ignored unless the sensors are simulated.
Names deliberately do not start with ``RTE_`` so the documented L5 function list stays exact.
"""

import rte
from ioc import IOC_bIsSimulated, IOC_vSimInjectVibrationEdges, IOC_vSimSetDistanceCm

SIM_DISTANCE_MIN_CM = 2
SIM_DISTANCE_MAX_CM = 400
SIM_DISTANCE_SLIDER_MAX_CM = 1000


def SIM_bSensorsSimulated():
    """True when distance sensor, vibration inputs and GPIO are simulated."""
    return IOC_bIsSimulated()


def SIM_bSetDistanceCm(distance_cm):
    """Set the simulated distance: 0 = no echo, 1-1000 cm (values outside 2-400 read as invalid).

    Returns True if applied (False for bad values or when the sensor is real hardware).
    """
    if type(distance_cm) is not int or not 0 <= distance_cm <= SIM_DISTANCE_SLIDER_MAX_CM:
        return False
    if not IOC_bIsSimulated():
        return False
    IOC_vSimSetDistanceCm(distance_cm if distance_cm else None)
    return True


def SIM_bInjectVibrationEdges(count):
    """Inject `count` (1-50) vibration edges. Returns True if applied."""
    if type(count) is not int or not (1 <= count <= 50) or not IOC_bIsSimulated():
        return False
    IOC_vSimInjectVibrationEdges(count)
    return True


def SIM_tGetLinks():
    """(meshtastic modem link, zigbee link) of the RTE; either can be None before RTE_vInit."""
    return rte._mesh, rte._zigbee
