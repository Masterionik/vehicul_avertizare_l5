"""USB driver (serial port): XBee3 Zigbee and Meshtastic ESP module (pyserial).

Non-blocking polling access. Without pyserial or without the /dev/tty* port
the channel runs in simulation (in-memory TX/RX buffers).
Invalid arguments -> ValueError; closed port / I/O error -> USBError.
"""
import os
import threading

from . import config

try:
    import serial
except ImportError:          # development PC
    serial = None


class USBError(OSError):
    """USB port error (port missing, closed, I/O error)."""


_lock = threading.RLock()
_channels = {}               # channel -> {"sim", "ser", "rx", "tx"}


def _cfg(channel):
    return config.usb_config(channel)      # ValueError for an invalid channel


def _get_open(channel):
    ch = _channels.get(channel)
    if ch is None:
        _cfg(channel)
        raise USBError("USB channel %r is not open" % (channel,))
    return ch


def USB_vOpen(channel):
    """Open the channel (idempotent)."""
    cfg = _cfg(channel)
    with _lock:
        if channel in _channels:
            return
        if cfg["error"] and config.simulation_mode() not in ("1", "true", "yes", "on"):
            raise USBError(cfg["error"])       # ambiguous ports: the environment variable is required
        hw = serial is not None and os.path.exists(cfg["port"])
        if config.use_simulation(hw):
            _channels[channel] = {"sim": True, "ser": None,
                                  "rx": bytearray(), "tx": bytearray()}
            return
        if serial is None:
            raise USBError("pyserial is not installed (pip install pyserial)")
        try:
            ser = serial.Serial(cfg["port"], cfg["baud"], timeout=0, write_timeout=1.0)
        except (OSError, serial.SerialException) as exc:
            raise USBError("cannot open %s: %s" % (cfg["port"], exc)) from exc
        _channels[channel] = {"sim": False, "ser": ser, "rx": None, "tx": None}


def USB_vClose(channel):
    _cfg(channel)
    with _lock:
        ch = _channels.pop(channel, None)
        if ch and ch["ser"] is not None:
            ch["ser"].close()


def USB_vCloseAll():
    with _lock:
        for channel in list(_channels):
            USB_vClose(channel)


def USB_bIsOpen(channel):
    _cfg(channel)
    with _lock:
        return channel in _channels


def USB_bIsSimulated(channel):
    with _lock:
        return _get_open(channel)["sim"]


def USB_u16Write(channel, data):
    """Send bytes; return the number of bytes written."""
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise ValueError("data must be bytes")
    with _lock:
        ch = _get_open(channel)
        if ch["sim"]:
            ch["tx"].extend(data)
            return len(data)
        try:
            return int(ch["ser"].write(bytes(data)))
        except (OSError, serial.SerialException) as exc:
            raise USBError("USB write failed: %s" % exc) from exc


def USB_u16Available(channel):
    """Number of received bytes not yet read."""
    with _lock:
        ch = _get_open(channel)
        return len(ch["rx"]) if ch["sim"] else int(ch["ser"].in_waiting)


def USB_baRead(channel, max_len=256):
    """Read up to `max_len` bytes without blocking (b"" if nothing is available)."""
    if not isinstance(max_len, int) or isinstance(max_len, bool) or max_len < 1:
        raise ValueError("invalid max_len: %r" % (max_len,))
    with _lock:
        ch = _get_open(channel)
        if ch["sim"]:
            out = bytes(ch["rx"][:max_len])
            del ch["rx"][:max_len]
            return out
        try:
            n = min(int(ch["ser"].in_waiting), max_len)
            return bytes(ch["ser"].read(n)) if n else b""
        except (OSError, serial.SerialException) as exc:
            raise USBError("USB read failed: %s" % exc) from exc


def USB_vFlush(channel):
    """Discard received data that has not been read."""
    with _lock:
        ch = _get_open(channel)
        if ch["sim"]:
            ch["rx"].clear()
        else:
            ch["ser"].reset_input_buffer()


# --------------------------------------------------------- simulation helpers
def USB_vSimInject(channel, data):
    """Simulation only: put bytes into the receive buffer."""
    with _lock:
        ch = _get_open(channel)
        if not ch["sim"]:
            raise RuntimeError("USB_vSimInject is only available in simulation")
        ch["rx"].extend(data)


def USB_baSimTakeTx(channel):
    """Simulation only: return and clear what was sent."""
    with _lock:
        ch = _get_open(channel)
        if not ch["sim"]:
            raise RuntimeError("USB_baSimTakeTx is only available in simulation")
        out = bytes(ch["tx"])
        ch["tx"].clear()
        return out
