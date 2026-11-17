"""Simulated device transport used for CI and local development.

Everything stays in memory. With ``loopback=True`` whatever is sent can also be received, which
lets the whole TX -> RX chain be tested on a laptop. ``inject_rx`` simulates a packet arriving
from a remote node. ``sent_packets`` keeps the raw bytes of the last transmitted packets so tests
can inspect them after ``poll()`` has consumed the event queue.
"""

from __future__ import annotations

import threading
from collections import deque
from datetime import datetime, timezone

from .transport import Transport

_MAX_EVENTS = 500
_MAX_SENT = 100


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SimulatedTransport(Transport):
    """In-memory simulator matching the transport contract. Thread-safe."""

    def __init__(self, name: str = "sim", *, loopback: bool = False):
        super().__init__(name=name)
        self.loopback = bool(loopback)
        self._lock = threading.RLock()
        self._events: deque = deque(maxlen=_MAX_EVENTS)
        self.sent_packets: deque = deque(maxlen=_MAX_SENT)

    def open(self) -> bool:
        with self._lock:
            self._connected = True
        return True

    def close(self) -> None:
        with self._lock:
            self._connected = False

    def is_open(self) -> bool:
        return self._connected

    def info(self) -> dict:
        return {
            "id": self.name,
            "name": self.name,
            "state": "connected" if self._connected else "disconnected",
            "transport": "sim",
            "connected": self._connected,
            "simulated": True,
            "loopback": self.loopback,
        }

    def send_text(self, dest: str, text: str) -> int:
        if not self._connected:
            return 0
        payload = str(text)
        with self._lock:
            self._events.append({
                "kind": "tx", "source": self.name, "destination": str(dest),
                "payload": payload, "ts": _now(),
            })
            if self.loopback:
                self._events.append({
                    "kind": "rx", "source": self.name, "portnum": 1,
                    "payload": payload.encode("utf-8").hex(), "text": payload, "ts": _now(),
                })
        return len(payload)

    def send_packet(self, dest: str, payload: bytes, portnum: int = 256) -> int:
        if not self._connected:
            return 0
        raw = bytes(payload)
        with self._lock:
            self.sent_packets.append(raw)
            self._events.append({
                "kind": "tx", "source": self.name, "destination": str(dest),
                "payload": raw.hex(), "portnum": int(portnum), "ts": _now(),
            })
            if self.loopback:
                self._events.append({
                    "kind": "rx", "source": self.name, "portnum": int(portnum),
                    "payload": raw.hex(), "ts": _now(),
                })
        return len(raw)

    def inject_rx(self, payload: bytes, portnum: int = 256) -> None:
        """Simulate bytes received from a remote node (works while the transport is open)."""
        with self._lock:
            if not self._connected:
                return
            self._events.append({
                "kind": "rx", "source": self.name, "portnum": int(portnum),
                "payload": bytes(payload).hex(), "ts": _now(),
            })

    def poll(self) -> list[dict]:
        with self._lock:
            events = list(self._events)
            self._events.clear()
        return events

    def get_config(self) -> dict:
        return {
            "region": "EU_868",
            "preset": "LONG_FAST",
            "channel_name": "VEH",
            "transport": "sim",
        }

    def apply_config(self, diff: dict) -> dict:
        config = self.get_config()
        for key, value in diff.items():
            if isinstance(value, dict):
                config[key] = value.get("target", value.get("value", config.get(key)))
            else:
                config[key] = value
        return config
