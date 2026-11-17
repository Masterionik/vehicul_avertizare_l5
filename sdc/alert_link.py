"""Shared alert send/receive logic for the Meshtastic and Zigbee links.

A link subclass provides the transport specific parts:

* ``_send_wire(payload)``: put one signed alert packet on the air, return bytes sent (0 on failure)
* ``_rx_frames(events)``: turn raw RX events into complete alert payloads
* ``poll()`` / ``is_connected()``

This mixin adds sequence numbers, signing, signature and replay checks, counters and a queue of
valid received alerts. Invalid, tampered or replayed packets are counted and dropped.
"""

from __future__ import annotations

import threading
from collections import deque

from .packets import BadSignatureError, PacketError, ReplayGuard, decode_alert, encode_alert

_MAX_QUEUE = 64


class AlertLinkMixin:
    def _init_alerts(self, node_id: int) -> None:
        self._alert_lock = threading.RLock()
        self._node_id = int(node_id)
        self._seq = 0
        self._guard = ReplayGuard()
        self._alerts: deque = deque(maxlen=_MAX_QUEUE)
        self._stats = {"tx": 0, "tx_fail": 0, "rx_ok": 0, "rx_bad_signature": 0,
                       "rx_bad_format": 0, "rx_replay": 0}

    # -- hooks implemented by the concrete link -------------------------------------------
    def _send_wire(self, payload: bytes) -> int:  # pragma: no cover - abstract
        raise NotImplementedError

    def _rx_frames(self, events: list[dict]) -> list[bytes]:  # pragma: no cover - abstract
        raise NotImplementedError

    # -- public API ------------------------------------------------------------------------
    @property
    def node_id(self) -> int:
        return self._node_id

    def send_alert(self, kind: int, level: int, distance_cm: int) -> int:
        """Sign and send an alert. Returns bytes put on the link, 0 on invalid args or no link."""
        with self._alert_lock:
            if not self.is_connected():
                return 0
            seq = (self._seq + 1) & 0xFF
            try:
                payload = encode_alert(seq, kind, level, distance_cm, self._node_id)
            except PacketError:
                return 0
            try:
                sent = int(self._send_wire(payload))
            except Exception:
                sent = 0
            if sent > 0:
                self._seq = seq
                self._stats["tx"] += 1
            else:
                self._stats["tx_fail"] += 1
            return sent

    def recv_alert(self):
        """Return the oldest valid received alert dict, or None. Never blocks."""
        with self._alert_lock:
            self._pump()
            return self._alerts.popleft() if self._alerts else None

    def stats(self) -> dict:
        with self._alert_lock:
            return dict(self._stats)

    def reset_alerts(self) -> None:
        with self._alert_lock:
            self.poll()                       # discard RX events that are still queued
            self._seq = 0
            self._guard.reset()
            self._alerts.clear()
            for key in self._stats:
                self._stats[key] = 0

    # -- internals -------------------------------------------------------------------------
    def _pump(self) -> None:
        events = [e for e in self.poll() if e.get("kind") == "rx"]
        if not events:
            return
        for payload in self._rx_frames(events):
            try:
                alert = decode_alert(payload)
            except BadSignatureError:
                self._stats["rx_bad_signature"] += 1
                continue
            except PacketError:
                self._stats["rx_bad_format"] += 1
                continue
            if not self._guard.accept(alert["node"], alert["seq"]):
                self._stats["rx_replay"] += 1
                continue
            self._stats["rx_ok"] += 1
            self._alerts.append(alert)
