"""NODE: the runtime step, called every 100 ms by ONE background thread (started from web/apps.py).

``NODE_vStep`` runs PRX -> IMP -> ALR -> RPT once. ``NODE_vStart`` / ``NODE_vStop`` manage the single
runtime thread and the RTE lifecycle; the thread forces buzzer and LED off when it stops, also after
an unexpected error. Web views read ``NODE_tGetStatus`` / ``NODE_lGetEvents`` and never touch hardware.
"""

import threading
import time
from collections import deque

from rte import (RTE_DISTANCE_INVALID, RTE_u8GetTick60s, RTE_u8GetTick100ms, RTE_u16ReadDistanceCm,
                 RTE_vDeinit, RTE_vInit)

from .alr import ALR_vShutdown, ALR_vStep, AlertManager
from .imp import IMP, IMP_vStep
from .prx import PRX, PRX_vStep
from .rpt import RPT

NODE_STEP_S = 0.1
NODE_EVENTS_MAX = 100
NODE_PULSE_MAX_MS = 2000

_lock = threading.RLock()
_thread = None
_stop = threading.Event()


class NodeRuntime:
    """All application state of the node; one instance is used by the runtime thread."""

    def __init__(self):
        self.prx = PRX()
        self.imp = IMP()
        self.alr = AlertManager()
        self.rpt = RPT()
        self.events = deque(maxlen=NODE_EVENTS_MAX)
        self.last_report = None
        self.distance_cm = RTE_DISTANCE_INVALID
        self.level = 0
        self.impact = 0
        self.buzzer = 0
        self.led = 0
        self.steps = 0
        self._fault_prev = False
        self.pulses = {}                 # output name -> end time (ms) of a manual test pulse

    def log(self, text):
        self.events.append({"time": time.time(), "text": text})

    def step(self, now_ms):
        old_level = self.level
        self.level = PRX_vStep(self.prx, now_ms)
        self.distance_cm = RTE_u16ReadDistanceCm()
        valid = self.distance_cm != RTE_DISTANCE_INVALID
        self.rpt.add_sample(self.distance_cm, self.level, valid)
        if self.level != old_level:
            self.log("level %d -> %d" % (old_level, self.level))
        if self.prx.fault and not self._fault_prev:
            self.rpt.add_fault()
            self.log("sensor fault")
        elif self._fault_prev and not self.prx.fault:
            self.log("sensor recovered")
        self._fault_prev = self.prx.fault

        if IMP_vStep(self.imp, now_ms):
            self.rpt.add_impact()
            self.log("impact detected")
        self.impact = 1 if self.imp.active(now_ms) else 0

        def on_rx(radio, alert):
            self.rpt.add_rx(radio)
            self.log("remote alert via %s: node %s kind %d level %d" % (
                radio, alert["node"], alert["kind"], alert["level"]))

        if self.alr.active:                                 # a real alert always wins over a test pulse
            self.pulses.clear()
        self.pulses = {k: t for k, t in self.pulses.items() if now_ms < t}
        actions = ALR_vStep(self.alr, now_ms, self.level, self.impact, self.distance_cm,
                            on_rx=on_rx, on_tx=lambda radio, kind, level: self.rpt.add_tx(radio),
                            buzzer_min=1 if "buzzer" in self.pulses else 0,
                            led_min=1 if "led" in self.pulses else 0)
        self.buzzer, self.led = actions["buzzer"], actions["led"]
        if RTE_u8GetTick60s():
            self.last_report = self.rpt.snapshot(reset=True)
            self.log("report: %d samples, max level %d, %d impacts" % (
                self.last_report["samples"], self.last_report["max_level"], self.last_report["impacts"]))
        self.steps += 1


def _now_ms():
    return int(time.monotonic() * 1000)


_runtime = NodeRuntime()


def NODE_vStep(now_ms=None):
    """Run one application step (PRX, IMP, ALR, RPT). Called every 100 ms by the runtime thread."""
    with _lock:
        _runtime.step(_now_ms() if now_ms is None else now_ms)


def _loop(stop):
    try:
        while not stop.is_set():
            if RTE_u8GetTick100ms():
                try:
                    NODE_vStep()
                except Exception as exc:                  # keep running, but never leave outputs on
                    ALR_vShutdown()
                    _runtime.log("step error: %s" % exc)
            stop.wait(0.01)
    finally:
        ALR_vShutdown()


def NODE_vStart():
    """Initialize the RTE and start the single runtime thread (safe to call twice)."""
    global _thread, _runtime
    with _lock:
        if _thread is not None and _thread.is_alive():
            return
        RTE_vInit()
        _runtime = NodeRuntime()
        _stop.clear()
        _thread = threading.Thread(target=_loop, args=(_stop,), name="node-runtime", daemon=True)
        _thread.start()


def NODE_vStop():
    """Stop the runtime thread, force buzzer and LED off and deinitialize the RTE."""
    global _thread
    with _lock:
        _stop.set()
        thread, _thread = _thread, None
    if thread is not None:
        thread.join(timeout=2.0)
    ALR_vShutdown()
    RTE_vDeinit()


def NODE_bIsRunning():
    return _thread is not None and _thread.is_alive()


def NODE_bRequestPulse(output, duration_ms, now_ms=None):
    """Ask the runtime thread to switch "buzzer" or "led" on for 1-2000 ms (manual test).

    Refused (False) for bad arguments or while a real alert is active. The output is switched by the
    runtime thread, so web views never touch the hardware.
    """
    if output not in ("buzzer", "led") or type(duration_ms) is not int or not 1 <= duration_ms <= NODE_PULSE_MAX_MS:
        return False
    with _lock:
        if _runtime.alr.active:
            return False
        now = _now_ms() if now_ms is None else now_ms
        _runtime.pulses[output] = now + duration_ms
    return True


def NODE_vClearPulses():
    """Cancel manual test pulses and force buzzer and LED off (real alerts re-assert on the next step)."""
    with _lock:
        _runtime.pulses.clear()
    ALR_vShutdown()


def NODE_bAlertActive():
    """True while a real alert is active (used to refuse manual buzzer/LED tests)."""
    return _runtime.alr.active


def NODE_tGetStatus():
    """Snapshot for the dashboard: distance, level, buzzer, LED, impact, fault, last remote alert, report."""
    with _lock:
        r = _runtime
        return {
            "distance_cm": None if r.distance_cm == RTE_DISTANCE_INVALID else r.distance_cm,
            "level": r.level,
            "buzzer": r.buzzer,
            "led": r.led,
            "impact": r.impact,
            "sensor_fault": r.prx.fault,
            "last_remote_alert": dict(r.alr.last_remote) if r.alr.last_remote else None,
            "report": r.last_report,
            "counters": r.rpt.snapshot(),
            "vibration_edges_total": r.imp.edges_total,
            "impacts_total": r.imp.total,
            "steps": r.steps,
            "running": NODE_bIsRunning(),
        }


def NODE_lGetEvents():
    """Recent events, oldest first."""
    with _lock:
        return list(_runtime.events)
