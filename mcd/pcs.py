"""PCS driver: digital GPIO outputs/inputs (Raspberry Pi, lgpio library).

Implements PCS_vSetOutputGpio from lab L3: invalid parameters are ignored,
so a wrong command never changes the state of the outputs.
Without lgpio or without /dev/gpiochip* the driver runs in simulation.
"""
import glob
import os
import threading
import time

from . import config

try:
    import lgpio
except ImportError:          # development PC
    lgpio = None

PCS_PIN_OFF = 0
PCS_PIN_ON = 1
PCS_PIN_INVALID = 0xFF       # value returned for an invalid pin

_lock = threading.RLock()
_initialized = False
_simulated = True
_handle = None
_direction = {}              # pin -> "out" | "in"
_level = {}                  # pin -> level (output cache / simulated values)

# Edge counters (vibration sensors): rising edges, one per pulse.
PCS_EDGE_DEBOUNCE_NS = 5_000_000     # edges closer than 5 ms to the last counted edge are ignored
PCS_EDGE_COUNT_MAX = 0xFFFF          # the counter saturates (u16)
_POLL_PERIOD_S = 0.0005              # polling fallback: 0.5 ms
_edge_lock = threading.Lock()
_edges = {}                  # pin -> {"count", "last_ns", "mode", "cb", "thread", "stop"}


def _hw_available():
    return lgpio is not None and bool(glob.glob("/dev/gpiochip*"))


def _is_valid_pin(pin):
    return (isinstance(pin, int) and not isinstance(pin, bool)
            and config.PCS_PIN_MIN <= pin <= config.PCS_PIN_MAX)


def _is_valid_level(value):
    return isinstance(value, int) and value in (PCS_PIN_OFF, PCS_PIN_ON)


TARGET_GPIO_LABELS = {"pinctrl-rp1", "pinctrl-bcm2711"}


def _parse_chip_label(value):
    """Extract a chip label from the various structures returned by lgpio."""
    if value is None:
        return None
    if isinstance(value, dict):
        for key in ("label", "name", "chip_label", "chip_name"):
            entry = value.get(key)
            parsed = _parse_chip_label(entry)
            if parsed is not None:
                return parsed
        return None
    if isinstance(value, (list, tuple)):
        for entry in value:
            parsed = _parse_chip_label(entry)
            if parsed is not None:
                return parsed
        return None
    if hasattr(value, "label"):
        parsed = _parse_chip_label(getattr(value, "label"))
        if parsed is not None:
            return parsed
    if hasattr(value, "name"):
        parsed = _parse_chip_label(getattr(value, "name"))
        if parsed is not None:
            return parsed
    if isinstance(value, str):
        return value.strip().lower()
    return None


def _read_chip_label(chip_id):
    """Try to read the chip label from lgpio or from sysfs."""
    if lgpio is not None:
        for attempt in (
            lambda cid: lgpio.gpio_get_chip_info(cid),
            lambda cid: (lambda handle: lgpio.gpio_get_chip_info(handle))(lgpio.gpiochip_open(cid)),
        ):
            try:
                label = _parse_chip_label(attempt(chip_id))
                if label is not None:
                    return label
            except Exception:
                continue

    label_path = "/sys/class/gpio/gpiochip%d/label" % chip_id
    try:
        if os.path.exists(label_path):
            with open(label_path, "r", encoding="utf-8") as handle:
                return handle.read().strip().lower()
    except OSError:
        pass
    return None


def _resolve_gpiochip():
    """Return the number of the GPIO chip with the correct Pi 4/5 label."""
    env_value = os.environ.get("MCD_GPIOCHIP", "").strip()
    if env_value:
        raw = env_value.lower()
        if raw.startswith("gpiochip"):
            raw = raw.replace("gpiochip", "", 1)
        try:
            return int(raw)
        except ValueError:
            pass

    chips = []
    for path in sorted(glob.glob("/dev/gpiochip*")):
        chip_name = os.path.basename(path)
        if chip_name.startswith("gpiochip"):
            try:
                chips.append(int(chip_name.replace("gpiochip", "", 1)))
            except ValueError:
                continue

    for chip_id in chips:
        label = _read_chip_label(chip_id)
        if label in TARGET_GPIO_LABELS:
            return chip_id

    return chips[0] if chips else 4


def _open_chip():
    last = None
    candidates = []
    resolved = None
    try:
        resolved = _resolve_gpiochip()
    except Exception:
        resolved = None
    if resolved is not None:
        candidates.append(resolved)

    for path in sorted(glob.glob("/dev/gpiochip*")):
        chip_name = os.path.basename(path)
        if chip_name.startswith("gpiochip"):
            try:
                chip_id = int(chip_name.replace("gpiochip", "", 1))
            except ValueError:
                continue
            if chip_id not in candidates:
                candidates.append(chip_id)

    if not candidates:
        candidates = [4]

    seen = set()
    for chip in candidates:
        if chip in seen:
            continue
        seen.add(chip)
        try:
            return lgpio.gpiochip_open(chip)
        except Exception as exc:     # lgpio.error
            last = exc
    raise RuntimeError("cannot open gpiochip: %s" % (last,))


def _configure(pin, direction):
    """Configure the direction of a pin (called with _lock held)."""
    if _direction.get(pin) == direction:
        return
    if not _simulated:
        if pin in _direction:
            lgpio.gpio_free(_handle, pin)
        if direction == "out":
            lgpio.gpio_claim_output(_handle, pin, PCS_PIN_OFF)
        else:
            lgpio.gpio_claim_input(_handle, pin)
    _direction[pin] = direction
    if direction == "out":
        _level[pin] = PCS_PIN_OFF


def PCS_vInit():
    """Initialize the driver; the alert outputs start switched off."""
    global _initialized, _simulated, _handle
    with _lock:
        if _initialized:
            return
        _simulated = config.use_simulation(_hw_available())
        if not _simulated:
            if lgpio is None:
                raise RuntimeError("lgpio is not installed (pip install lgpio)")
            _handle = _open_chip()
        _initialized = True
        for pin in config.OUTPUT_PINS:
            _configure(pin, "out")


def PCS_vDeinit():
    """Switch the outputs off, release the pins and reset the driver state."""
    global _initialized, _simulated, _handle
    _stop_edge_counters()                # outside _lock: polling threads must be able to finish
    with _lock:
        if _initialized and not _simulated:
            for pin, direction in list(_direction.items()):
                if direction == "out":
                    lgpio.gpio_write(_handle, pin, PCS_PIN_OFF)
                lgpio.gpio_free(_handle, pin)
            lgpio.gpiochip_close(_handle)
        _initialized = False
        _simulated = True
        _handle = None
        _direction.clear()
        _level.clear()


def PCS_bIsSimulated():
    with _lock:
        if _initialized:
            return _simulated
        return config.use_simulation(_hw_available())


def PCS_vConfigInput(u8Pin):
    """Configure a pin as a digital input (invalid pin: ignored)."""
    if not _is_valid_pin(u8Pin):
        return
    with _lock:
        PCS_vInit()
        _configure(u8Pin, "in")


def PCS_vSetOutputGpio(u8Pin, value):
    """Set the level of an output pin. Invalid parameters are ignored."""
    if not _is_valid_pin(u8Pin) or not _is_valid_level(value):
        return
    with _lock:
        PCS_vInit()
        _configure(u8Pin, "out")
        if not _simulated:
            lgpio.gpio_write(_handle, u8Pin, int(value))
        _level[u8Pin] = int(value)


def PCS_u8GetGpio(u8Pin):
    """Read the level of a pin; PCS_PIN_INVALID (0xFF) for an invalid pin."""
    if not _is_valid_pin(u8Pin):
        return PCS_PIN_INVALID
    with _lock:
        PCS_vInit()
        if u8Pin not in _direction:
            _configure(u8Pin, "in")
        if _direction[u8Pin] == "out":
            return _level.get(u8Pin, PCS_PIN_OFF)
        if _simulated:
            return _level.get(u8Pin, PCS_PIN_OFF)
        return int(lgpio.gpio_read(_handle, u8Pin))


def PCS_vSimSetInput(u8Pin, value):
    """Simulation only: force the level of an input pin (for tests)."""
    if not _is_valid_pin(u8Pin) or not _is_valid_level(value):
        return
    with _lock:
        PCS_vInit()
        if not _simulated:
            raise RuntimeError("PCS_vSimSetInput is only available in simulation")
        if _direction.get(u8Pin) != "in":
            _configure(u8Pin, "in")
        rising = value == PCS_PIN_ON and _level.get(u8Pin, PCS_PIN_OFF) == PCS_PIN_OFF
        _level[u8Pin] = int(value)
    if rising:
        _edge_event(u8Pin, time.perf_counter_ns())


# ------------------------------------------------------------- edge counting
def _edge_event(u8Pin, ts_ns):
    """Register a rising edge seen at `ts_ns`; edges closer than 5 ms to the last counted one are dropped."""
    with _edge_lock:
        entry = _edges.get(u8Pin)
        if entry is None:
            return False
        last = entry["last_ns"]
        if last is not None and 0 <= ts_ns - last < PCS_EDGE_DEBOUNCE_NS:
            return False
        entry["last_ns"] = ts_ns
        if entry["count"] < PCS_EDGE_COUNT_MAX:
            entry["count"] += 1
        return True


def _poll_edges(u8Pin, handle, stop):
    """Fallback when lgpio callbacks are unavailable: fast polling for rising edges."""
    prev = 0
    while not stop.is_set():
        try:
            level = int(lgpio.gpio_read(handle, u8Pin))
        except Exception:
            return                       # chip closed or I/O error: the counter just stops
        if level and not prev:
            _edge_event(u8Pin, time.perf_counter_ns())
        prev = level
        time.sleep(_POLL_PERIOD_S)


def _start_hw_edge_source(u8Pin, entry):
    """Claim the pin as an alert input (pull-down, so an unconnected optional sensor stays low)."""
    pull = getattr(lgpio, "SET_PULL_DOWN", 0)
    if u8Pin in _direction:
        lgpio.gpio_free(_handle, u8Pin)
    lgpio.gpio_claim_alert(_handle, u8Pin, lgpio.RISING_EDGE, pull)
    _direction[u8Pin] = "in"

    def _on_edge(chip, gpio, level, timestamp):
        _edge_event(u8Pin, int(timestamp) if timestamp else time.perf_counter_ns())

    try:
        entry["cb"] = lgpio.callback(_handle, u8Pin, lgpio.RISING_EDGE, _on_edge)
        entry["mode"] = "callback"
    except Exception:
        stop = threading.Event()
        thread = threading.Thread(target=_poll_edges, args=(u8Pin, _handle, stop),
                                  name="pcs-edge-%d" % u8Pin, daemon=True)
        entry["stop"], entry["thread"], entry["mode"] = stop, thread, "poll"
        thread.start()


def _stop_edge_counters():
    with _edge_lock:
        entries = list(_edges.values())
        _edges.clear()
    for entry in entries:
        if entry.get("stop") is not None:
            entry["stop"].set()
        if entry.get("thread") is not None:
            entry["thread"].join(timeout=1.0)
        if entry.get("cb") is not None:
            try:
                entry["cb"].cancel()
            except Exception:
                pass


def PCS_vConfigEdgeCounter(u8Pin):
    """Start counting rising edges (pulses) on an input pin (invalid pin: ignored; idempotent).

    Edges closer than 5 ms to the previous counted edge are ignored (debounce in the driver).
    """
    if not _is_valid_pin(u8Pin):
        return
    with _lock:
        PCS_vInit()
        if u8Pin in _edges:
            return
        entry = {"count": 0, "last_ns": None, "mode": "sim", "cb": None, "thread": None, "stop": None}
        if _simulated:
            _configure(u8Pin, "in")
        else:
            _start_hw_edge_source(u8Pin, entry)
        with _edge_lock:
            _edges[u8Pin] = entry


def PCS_u16GetEdgeCount(u8Pin, clear=False):
    """Number of counted edges on the pin (0 if the pin has no edge counter); clear=True resets it."""
    if not _is_valid_pin(u8Pin):
        return 0
    with _edge_lock:
        entry = _edges.get(u8Pin)
        if entry is None:
            return 0
        count = entry["count"]
        if clear:
            entry["count"] = 0
        return count


def PCS_sEdgeCounterMode(u8Pin):
    """Edge source of the pin: "callback", "poll", "sim" or None (no edge counter)."""
    with _edge_lock:
        entry = _edges.get(u8Pin) if _is_valid_pin(u8Pin) else None
        return entry["mode"] if entry else None


def PCS_vSimInjectEdges(u8Pin, n):
    """Simulation only: add `n` edges to a counted pin (injected edges bypass the debounce)."""
    if not _is_valid_pin(u8Pin) or not isinstance(n, int) or isinstance(n, bool) or n < 0:
        return
    with _lock:
        PCS_vInit()
        if not _simulated:
            raise RuntimeError("PCS_vSimInjectEdges is only available in simulation")
    with _edge_lock:
        entry = _edges.get(u8Pin)
        if entry is not None:
            entry["count"] = min(PCS_EDGE_COUNT_MAX, entry["count"] + n)
