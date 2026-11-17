"""IOC layer: maps logical channels to MCD (buzzer, LED, distance sensor, vibration inputs).

Output channels are written with IOC_vSetOutputPort. Input channels are read with the
IOC_u16Get* / IOC_u8Get* functions. Unknown channels and invalid values are ignored.
"""

from mcd import config
from mcd import pcs
from mcd import ultrasonic

IOC_CH_BUZZER = 0x0001
IOC_CH_LED = 0x0002
IOC_CH_DISTANCE = 0x0010       # input channel: ultrasonic sensor
IOC_CH_VIBRATION = 0x0020      # input channel: vibration sensors

IOC_DISTANCE_INVALID = ultrasonic.US_DISTANCE_INVALID

_IOC_MAP = {
    IOC_CH_BUZZER: config.GPIO_BUZZER,
    IOC_CH_LED: config.GPIO_LED,
}


def IOC_vInit():
    """Initialize the sensors: ultrasonic pins and the edge counters of the vibration inputs."""
    pcs.PCS_vInit()
    ultrasonic.US_vInit()
    for pin in config.vibration_pins():
        pcs.PCS_vConfigEdgeCounter(pin)


def IOC_vDeinit():
    """Switch the outputs off and release the sensors and the GPIO driver."""
    for pin in _IOC_MAP.values():
        pcs.PCS_vSetOutputGpio(pin, 0)
    ultrasonic.US_vDeinit()
    pcs.PCS_vDeinit()


def IOC_vSetOutputPort(channel, value):
    """Map MCU channel to the correct GPIO pin. Unknown or invalid values are ignored."""
    if not isinstance(channel, int) or channel not in _IOC_MAP:
        return
    if type(value) is not int or value not in (0, 1):
        return
    pcs.PCS_vSetOutputGpio(_IOC_MAP[channel], value)


def IOC_u16GetDistanceCm():
    """Distance in cm (2-400) from the ultrasonic sensor, IOC_DISTANCE_INVALID (0xFFFF) if invalid."""
    return ultrasonic.US_u16ReadDistanceCm()


def IOC_u8GetDistanceStatus():
    """1 if the last distance measurement was valid, else 0."""
    return 1 if ultrasonic.US_bLastReadValid() else 0


def IOC_u16GetVibrationEdges():
    """Edges counted on all vibration inputs since the previous call (the count is consumed)."""
    pins = config.vibration_pins()
    for pin in pins:
        pcs.PCS_vConfigEdgeCounter(pin)      # idempotent: also covers a missing IOC_vInit
    return min(0xFFFF, sum(pcs.PCS_u16GetEdgeCount(pin, True) for pin in pins))


def IOC_u8GetVibrationState():
    """Current raw level of the vibration inputs: 1 if any input is high, else 0."""
    for pin in config.vibration_pins():
        pcs.PCS_vConfigEdgeCounter(pin)
        if pcs.PCS_u8GetGpio(pin) == 1:
            return 1
    return 0


# ------------------------------------------------------------------ simulation support
def IOC_bIsSimulated():
    """True when the sensors and GPIO run in simulation (no real hardware behind them)."""
    return bool(pcs.PCS_bIsSimulated())


def IOC_vSimSetDistanceCm(distance_cm):
    """Simulation only: set the distance returned by the ultrasonic sensor. Ignored on real hardware."""
    if IOC_bIsSimulated():
        ultrasonic.US_vSimSetDistanceCm(distance_cm)


def IOC_vSimInjectVibrationEdges(count):
    """Simulation only: add `count` edges to the first vibration input. Ignored on real hardware."""
    if type(count) is not int or count < 0 or not IOC_bIsSimulated():
        return
    pins = config.vibration_pins()
    if pins:
        pcs.PCS_vConfigEdgeCounter(pins[0])
        pcs.PCS_vSimInjectEdges(pins[0], count)
