"""MCD configuration: pin mapping, channels and simulation mode."""
import os

# --------------------------------------------------------------- simulation
# MCD_SIMULATE = "auto" (default): real hardware if the library and the device
#                exist, otherwise simulation (running on a PC);
#                "1": forced simulation; "0": forced hardware (error if missing).
SIM_ENV = "MCD_SIMULATE"


def simulation_mode():
    return os.environ.get(SIM_ENV, "auto").strip().lower()


def use_simulation(hw_available):
    """Decide whether a driver runs in simulation."""
    mode = simulation_mode()
    if mode in ("1", "true", "yes", "on"):
        return True
    if mode in ("0", "false", "no", "off"):
        return False
    return not hw_available


# --------------------------------------------------------------------- GPIO
PCS_PIN_MIN = 0
PCS_PIN_MAX = 27          # valid range from L3 (BCM numbering)

GPIO_BUZZER = 17          # buzzer through an NPN transistor
GPIO_LED = 27             # LED with a 330 ohm resistor
GPIO_US_TRIG = 23         # HC-SR04 TRIG (3.3 V output is enough to trigger it)
GPIO_US_ECHO = 24         # HC-SR04 ECHO (5 V signal: needs a 1 kOhm + 2 kOhm divider)
GPIO_VIB = (22, 25)       # digital vibration sensors (SW-420 type); the second is optional
OUTPUT_PINS = (GPIO_BUZZER, GPIO_LED)

VIB2_ENV = "MCD_VIB2"     # "0" = the optional second vibration sensor is not fitted


def vibration_pins():
    """GPIO pins of the vibration sensors that are in use (the second one is optional)."""
    if os.environ.get(VIB2_ENV, "1").strip().lower() in ("0", "false", "no", "off"):
        return GPIO_VIB[:1]
    return tuple(GPIO_VIB)


# --------------------------------------------------------------------- USB
USB_CH_ZIGBEE = 0         # XBee3 through a USB adapter
USB_CH_MESHTASTIC = 1     # Meshtastic modem (Heltec WiFi LoRa 32 V3)

# Serial port names are NOT stable (/dev/ttyUSB0 depends on the plug order), so the ports are
# identified through /dev/serial/by-id/* (vendor / product strings). Resolution order:
#   1. environment variable (ZIGBEE_PORT / MESHTASTIC_PORT, legacy MCD_ZIGBEE_PORT / MCD_MESHTASTIC_PORT)
#   2. exactly one matching device in /dev/serial/by-id
#   3. the fallback name below (only a last resort)
# If several devices match and nothing selects one, the environment variable is REQUIRED
# and the configuration carries an error message that says so.
_USB_DEFAULTS = {
    USB_CH_ZIGBEE: {"name": "ZIGBEE", "port": "/dev/ttyUSB0", "baud": 9600,
                    "envs": ("ZIGBEE_PORT", "MCD_ZIGBEE_PORT"), "kind": "xbee"},
    USB_CH_MESHTASTIC: {"name": "MESHTASTIC", "port": "/dev/ttyACM0", "baud": 115200,
                        "envs": ("MESHTASTIC_PORT", "MCD_MESHTASTIC_PORT"), "kind": "meshtastic"},
}

SERIAL_BY_ID_DIR = "/dev/serial/by-id"
SERIAL_BY_ID_ENV = "MCD_SERIAL_BY_ID_DIR"      # override of the directory (tests)

# Lower-case substrings of the by-id names. The XBee USB adapters use FTDI chips; the Meshtastic
# boards use Silicon Labs CP210x (Heltec V3), Espressif native USB (e-ink), Seeed nRF52840 (XIAO).
_XBEE_HINTS = ("xbee", "digi", "ftdi", "ft232", "ft231", "ft2232")
_MESHTASTIC_HINTS = ("heltec", "seeed", "xiao", "espressif", "nrf52", "rak", "lilygo", "t-beam",
                     "silicon_labs", "cp210", "ch340", "ch910", "wch", "1a86")
# The Heltec V3 (the Pi's own modem) uses a CP210x bridge: preferred when several boards are plugged.
_MODEM_HINTS = ("cp210", "silicon_labs")


class PortSelectionError(ValueError):
    """Several serial devices match and nothing selects one (set the environment variable)."""


def classify_serial_id(name):
    """Classify a /dev/serial/by-id name: "xbee", "meshtastic" or "unknown"."""
    low = str(name).lower()
    if any(hint in low for hint in _XBEE_HINTS):
        return "xbee"
    if any(hint in low for hint in _MESHTASTIC_HINTS):
        return "meshtastic"
    return "unknown"


def list_serial_devices():
    """USB serial devices from /dev/serial/by-id: [{id, path, realpath, kind}], sorted by id."""
    directory = os.environ.get(SERIAL_BY_ID_ENV, SERIAL_BY_ID_DIR)
    try:
        names = sorted(os.listdir(directory))
    except OSError:
        return []
    out = []
    for name in names:
        path = os.path.join(directory, name)
        out.append({"id": name, "path": path, "realpath": os.path.realpath(path),
                    "kind": classify_serial_id(name)})
    return out


def detect_port(kind):
    """Return the by-id path of the device of this kind, or None if there is none.

    Raises PortSelectionError when the choice is ambiguous.
    """
    candidates = [d for d in list_serial_devices() if d["kind"] == kind]
    if len(candidates) > 1 and kind == "meshtastic":
        preferred = [d for d in candidates if any(h in d["id"].lower() for h in _MODEM_HINTS)]
        if len(preferred) == 1:
            candidates = preferred
    if not candidates:
        return None
    if len(candidates) > 1:
        raise PortSelectionError(
            "%d %s devices found (%s): set the environment variable explicitly"
            % (len(candidates), kind, ", ".join(d["id"] for d in candidates)))
    return candidates[0]["path"]


def usb_channels():
    return tuple(_USB_DEFAULTS)


def usb_config(channel):
    """Return {name, port, baud, source, error} for a channel.

    "source" is "env", "by-id" or "default". "error" is None, or a message when the port is
    ambiguous and must be selected with the environment variable (the port is then the fallback name).
    """
    if channel not in _USB_DEFAULTS:
        raise ValueError("invalid USB channel: %r" % (channel,))
    spec = _USB_DEFAULTS[channel]
    cfg = {"name": spec["name"], "port": spec["port"], "baud": spec["baud"],
           "source": "default", "error": None}
    for env in spec["envs"]:
        value = os.environ.get(env, "").strip()
        if value:
            cfg["port"], cfg["source"] = value, "env"
            return cfg
    try:
        found = detect_port(spec["kind"])
    except PortSelectionError as exc:
        cfg["error"] = "%s: %s (%s)" % (spec["name"], exc, " or ".join(spec["envs"]))
        return cfg
    if found:
        cfg["port"], cfg["source"] = found, "by-id"
    return cfg


# ------------------------------------------------------------ pin mapping table
# Format required by L4: Pin | Signal type | Signal name | Definition
# Types: DOP = digital output, DIP = digital input, COM = communication,
#        PWR = power supply
PIN_TABLE = [
    {"pin": "GPIO17 (pin 11)", "signal_type": "DOP", "signal_name": "BUZZER",
     "definition": "Audible alert: 1 kOhm resistor to NPN transistor base, "
                   "active buzzer between 5 V and collector, protection diode"},
    {"pin": "GPIO27 (pin 13)", "signal_type": "DOP", "signal_name": "LED",
     "definition": "Visual alert: LED with a 330 Ohm resistor to GND"},
    {"pin": "GPIO23 (pin 16)", "signal_type": "DOP", "signal_name": "US_TRIG",
     "definition": "Ultrasonic sensor TRIG: 10 us trigger pulse"},
    {"pin": "GPIO24 (pin 18)", "signal_type": "DIP", "signal_name": "US_ECHO",
     "definition": "Ultrasonic sensor ECHO (5 V signal): 1 kOhm in series, then 2 kOhm to GND "
                   "(voltage divider), so the Pi pin sees about 3.3 V; high time = distance"},
    {"pin": "GPIO22 (pin 15)", "signal_type": "DIP", "signal_name": "VIB1",
     "definition": "Vibration sensor 1 (digital output, e.g. SW-420): impact pulses, counted as edges"},
    {"pin": "GPIO25 (pin 22)", "signal_type": "DIP", "signal_name": "VIB2",
     "definition": "Optional vibration sensor 2 (digital output): impact pulses, counted as edges"},
    {"pin": "5V (pin 2)", "signal_type": "PWR", "signal_name": "5V",
     "definition": "Supply of the ultrasonic sensor VCC and of the buzzer"},
    {"pin": "3V3 (pin 1)", "signal_type": "PWR", "signal_name": "3V3",
     "definition": "Supply of the vibration sensor modules (3.3 V logic levels)"},
    {"pin": "GND (pin 6)", "signal_type": "PWR", "signal_name": "GND",
     "definition": "Common ground (Pi, LED, buzzer transistor, sensors)"},
    {"pin": "USB (powered hub)", "signal_type": "COM", "signal_name": "ZIGBEE",
     "definition": "XBee3 Zigbee on a USB adapter, 9600 baud; found through /dev/serial/by-id "
                   "(override: ZIGBEE_PORT)"},
    {"pin": "USB (powered hub)", "signal_type": "COM", "signal_name": "MESHTASTIC",
     "definition": "Heltec WiFi LoRa 32 V3 (LoRa Meshtastic modem), 115200 baud; found through "
                   "/dev/serial/by-id (override: MESHTASTIC_PORT)"},
]
