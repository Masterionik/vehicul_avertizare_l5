"""Ultrasonic driver: HC-SR04 compatible distance sensor on top of PCS.

Wiring: TRIG = GPIO23, ECHO = GPIO24 (ECHO is a 5 V signal: 1 kOhm in series, then 2 kOhm to GND,
so the Pi pin sees about 3.3 V). Sensor VCC = 5 V.

Single-shot measurement:
  1. TRIG high for >= 10 us (busy wait on time.perf_counter_ns), then low.
  2. Wait for ECHO to go high (timeout 30 ms: no sensor / no echo -> invalid).
  3. Measure how long ECHO stays high (timeout 30 ms -> invalid).
  4. distance_cm = echo_us / 58, valid range 2-400 cm, anything else is invalid.

Invalid results are reported as US_DISTANCE_INVALID (0xFFFF) and US_bLastReadValid() is False.

Timing jitter: the pulse is timed by polling the pin from Python on a non-real-time OS, so the
result jitters by about +-1-2 cm (one GPIO read costs some microseconds, 1 cm = 58 us of echo time,
and the scheduler can delay the loop). Filtering is done by the application (PRX debounce).

Simulation (MCD_SIMULATE=1 or no GPIO): the sensor returns a settable distance (default 300 cm).
The driver is thread-safe: measurements are serialized with a lock.
"""
import threading
import time

from . import config, pcs

US_DISTANCE_INVALID = 0xFFFF
US_MIN_CM = 2
US_MAX_CM = 400
US_SIM_DEFAULT_CM = 300

US_TRIGGER_PULSE_NS = 12_000         # >= 10 us required by the sensor
US_TIMEOUT_NS = 30_000_000           # 30 ms (echo of 400 cm = 23.3 ms)
_US_PER_CM = 58

_lock = threading.RLock()
_initialized = False
_simulated = True
_sim_distance = US_SIM_DEFAULT_CM    # None = "no echo"
_last_valid = False

# Pin access used by the real measurement; replaceable by tests (see _use_backend).
_io = {"write": pcs.PCS_vSetOutputGpio, "read": pcs.PCS_u8GetGpio, "now": time.perf_counter_ns}


def _use_backend(write, read, now_ns):
    """Tests only: replace the pin access and force the hardware (non-simulated) code path."""
    global _initialized, _simulated
    with _lock:
        _io.update(write=write, read=read, now=now_ns)
        _simulated = False
        _initialized = True


def _restore_backend():
    _io.update(write=pcs.PCS_vSetOutputGpio, read=pcs.PCS_u8GetGpio, now=time.perf_counter_ns)


def US_vInit():
    """Initialize the sensor pins (idempotent). TRIG starts low, ECHO is an input."""
    global _initialized, _simulated
    with _lock:
        if _initialized:
            return
        _simulated = pcs.PCS_bIsSimulated()
        if not _simulated:
            pcs.PCS_vSetOutputGpio(config.GPIO_US_TRIG, 0)
            pcs.PCS_vConfigInput(config.GPIO_US_ECHO)
        _initialized = True


def US_vDeinit():
    """Release the sensor and reset the simulation state (TRIG is left low)."""
    global _initialized, _simulated, _sim_distance, _last_valid
    with _lock:
        if _initialized and not _simulated:
            _io["write"](config.GPIO_US_TRIG, 0)
        _restore_backend()
        _initialized = False
        _simulated = True
        _sim_distance = US_SIM_DEFAULT_CM
        _last_valid = False


def US_bIsSimulated():
    with _lock:
        return _simulated if _initialized else pcs.PCS_bIsSimulated()


def US_vSimSetDistanceCm(distance_cm):
    """Simulation only: set the distance the sensor "sees". None = no echo. Invalid values are ignored.

    Values outside 2-400 cm are accepted: they model an out-of-range target and read as invalid.
    """
    global _sim_distance
    if distance_cm is not None and (not isinstance(distance_cm, int) or isinstance(distance_cm, bool)
                                    or not 0 <= distance_cm < US_DISTANCE_INVALID):
        return
    with _lock:
        _sim_distance = distance_cm


def _to_distance_cm(echo_us):
    cm = int(round(echo_us / _US_PER_CM))
    return cm if US_MIN_CM <= cm <= US_MAX_CM else US_DISTANCE_INVALID


def _measure_echo_us():
    """One trigger + echo timing on the real pins. Returns the echo high time in us, or None on timeout."""
    write, read, now = _io["write"], _io["read"], _io["now"]
    trig, echo = config.GPIO_US_TRIG, config.GPIO_US_ECHO
    write(trig, 0)
    write(trig, 1)
    t0 = now()
    while now() - t0 < US_TRIGGER_PULSE_NS:
        pass
    write(trig, 0)
    t0 = now()
    while read(echo) == 0:                       # wait for the rising edge
        if now() - t0 > US_TIMEOUT_NS:
            return None
    rise = now()
    while read(echo) == 1:                       # measure the high time
        if now() - rise > US_TIMEOUT_NS:
            return None
    return (now() - rise) / 1000.0


def US_u16ReadDistanceCm():
    """Measure once. Distance in cm (2-400) or US_DISTANCE_INVALID (no echo / out of range)."""
    global _last_valid
    with _lock:
        US_vInit()
        if _simulated:
            cm = US_DISTANCE_INVALID if _sim_distance is None else _sim_distance
            if cm != US_DISTANCE_INVALID and not US_MIN_CM <= cm <= US_MAX_CM:
                cm = US_DISTANCE_INVALID
        else:
            echo_us = _measure_echo_us()
            cm = US_DISTANCE_INVALID if echo_us is None else _to_distance_cm(echo_us)
        _last_valid = cm != US_DISTANCE_INVALID
        return cm


def US_bLastReadValid():
    """True if the last measurement was valid (False before the first one)."""
    with _lock:
        return _last_valid
