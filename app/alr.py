"""ALR: alert manager. Buzzer and LED patterns (no PWM), radio alerts, remote alert handling.

Patterns: level 1 = 200 ms on / 800 ms off, level 2 = 200 / 300, level 3 = continuous.
Impact = 2 s continuous buzzer + rapid LED flashing. A remote alert = two short beeps and the LED on
for 5 s. Radio alerts are sent while level >= 2 or an impact is active, Zigbee at most once per
1 s and Meshtastic at most once per 5 s (LoRa airtime is scarce).

``AlertManager`` is pure logic (explicit time, returns actions); ``ALR_vStep`` does the RTE I/O.
Buzzer and LED are ALWAYS forced off by ``ALR_vShutdown``.
"""

from rte import (RTE_KIND_IMPACT, RTE_KIND_PROXIMITY, RTE_tGetMeshAlert, RTE_tGetZigbeeAlert,
                 RTE_u16SendMeshAlert, RTE_u16SendZigbeeAlert, RTE_vSetBuzzer, RTE_vSetLed,
                 RTE_vWriteRemoteAlertLevel)

ALR_LEVEL_PATTERNS_MS = {1: (200, 1000), 2: (200, 500)}     # level: (on time, period)
ALR_IMPACT_BUZZER_MS = 2000
ALR_IMPACT_LED_TOGGLE_MS = 100
ALR_REMOTE_BEEP_MS = 150                                    # two beeps: on, off, on, off, each 150 ms
ALR_REMOTE_LED_MS = 5000
ALR_TX_PERIOD_MS = {"zigbee": 1000, "mesh": 5000}
ALR_RX_DRAIN_MAX = 8


class AlertManager:
    def __init__(self):
        self._level = 0
        self._level_since = 0
        self._impact_prev = 0
        self._impact_start = None
        self._remote_start = None
        self._remote_led_until = -1
        self._remote_level = 0
        self.last_remote = None
        self._last_tx = dict.fromkeys(ALR_TX_PERIOD_MS)
        self._now_ms = 0

    def on_remote(self, now_ms, alert, radio):
        """Register a valid alert received from another node over ``radio``."""
        level = alert["level"]
        self.last_remote = {"radio": radio, "received_ms": now_ms, **alert}
        if level >= 1 or alert["kind"] == RTE_KIND_IMPACT:
            self._remote_start = now_ms
            self._remote_led_until = now_ms + ALR_REMOTE_LED_MS
            self._remote_level = max(level, 1)

    def remote_level(self, now_ms):
        return self._remote_level if now_ms < self._remote_led_until else 0

    def _remote_beep(self, now_ms):
        if self._remote_start is None:
            return 0
        step = (now_ms - self._remote_start) // ALR_REMOTE_BEEP_MS
        return 1 if 0 <= step < 4 and step % 2 == 0 else 0

    def update(self, now_ms, level, impact, distance_cm):
        """Compute outputs. Returns ``{"buzzer", "led", "send": [(radio, kind, level, distance_cm)]}``."""
        self._now_ms = now_ms
        if level != self._level:
            self._level, self._level_since = level, now_ms
        if impact and not self._impact_prev:
            self._impact_start = now_ms
        self._impact_prev = 1 if impact else 0

        impact_on = self._impact_start is not None and now_ms - self._impact_start < ALR_IMPACT_BUZZER_MS
        impact_led = impact_on and ((now_ms - self._impact_start) // ALR_IMPACT_LED_TOGGLE_MS) % 2 == 0

        if level >= 3:
            local = 1
        elif level in ALR_LEVEL_PATTERNS_MS:
            on_ms, period = ALR_LEVEL_PATTERNS_MS[level]
            local = 1 if (now_ms - self._level_since) % period < on_ms else 0
        else:
            local = 0

        buzzer = 1 if (impact_on or local or self._remote_beep(now_ms)) else 0
        led = 1 if (impact_led or local or now_ms < self._remote_led_until) else 0

        send = []
        if level >= 2 or impact_on:
            kind, send_level = (RTE_KIND_IMPACT, 3) if impact_on else (RTE_KIND_PROXIMITY, level)
            for radio, period in ALR_TX_PERIOD_MS.items():
                last = self._last_tx[radio]
                if last is None or now_ms - last >= period:
                    self._last_tx[radio] = now_ms
                    send.append((radio, kind, send_level, distance_cm))
        return {"buzzer": buzzer, "led": led, "send": send}

    @property
    def active(self):
        return bool(self._level >= 1 or self._impact_prev or self._now_ms < self._remote_led_until)


_SENDERS = {"zigbee": RTE_u16SendZigbeeAlert, "mesh": RTE_u16SendMeshAlert}


def ALR_vShutdown():
    """Force buzzer and LED off. Safe to call at any time."""
    RTE_vSetBuzzer(0)
    RTE_vSetLed(0)


def ALR_vStep(manager, now_ms, level, impact, distance_cm, on_rx=None, on_tx=None, buzzer_min=0, led_min=0):
    """Poll both radios for remote alerts, apply buzzer/LED outputs, send alerts.

    ``on_rx(radio, alert)`` / ``on_tx(radio, kind, level)`` are optional notification hooks.
    ``buzzer_min`` / ``led_min`` (0/1) keep an output on for a manual test pulse; they are OR-ed with the pattern.
    Returns the actions dict from ``AlertManager.update``.
    """
    for radio, getter in (("zigbee", RTE_tGetZigbeeAlert), ("mesh", RTE_tGetMeshAlert)):
        for _ in range(ALR_RX_DRAIN_MAX):
            alert = getter()
            if alert is None:
                break
            manager.on_remote(now_ms, alert, radio)
            if on_rx:
                on_rx(radio, alert)
    RTE_vWriteRemoteAlertLevel(manager.remote_level(now_ms))
    actions = manager.update(now_ms, level, impact, distance_cm)
    actions["buzzer"] |= 1 if buzzer_min else 0
    actions["led"] |= 1 if led_min else 0
    RTE_vSetBuzzer(actions["buzzer"])
    RTE_vSetLed(actions["led"])
    for radio, kind, send_level, distance in actions["send"]:
        sent = _SENDERS[radio](kind, send_level, distance)
        if sent > 0 and on_tx:
            on_tx(radio, kind, send_level)
    return actions
