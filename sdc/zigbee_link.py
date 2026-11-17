"""Zigbee link: XBee3 (9600 baud, transparent mode) over a USB serial adapter.

Backend selection (same rule as the other drivers, ``MCD_SIMULATE``):

* ``1``    -> simulated transport
* ``0``    -> real serial port (connect() fails with an error message if it is missing)
* ``auto`` -> real only if the XBee port exists, otherwise simulated

``info()["simulated"]`` is True whenever the simulated transport is in use, so the UI can show a
"SIMULATED" badge instead of pretending a radio is present.

Alerts are the signed 14-byte packet wrapped in a sync/length/CRC frame (see ``packets``) because
a serial link is a byte stream and chunks can be split or preceded by garbage.
"""

from __future__ import annotations

import os
from typing import Any

from mcd import config

from .alert_link import AlertLinkMixin
from .packets import FrameDecoder, frame_encode, node_id_from_name
from .transport_sim import SimulatedTransport
from .transport_zigbee import ZigbeeTransport


class ZigbeeLink(AlertLinkMixin):
    def __init__(self, *, port: str | None = None, name: str = "zigbee", node_id: int | None = None,
                 simulated: bool | None = None, loopback: bool = False):
        self.name = name
        self._config_error: str | None = None
        if port:
            self.port = port
        else:
            cfg = config.usb_config(config.USB_CH_ZIGBEE)
            self.port = cfg["port"]
            self._config_error = cfg["error"]
        if simulated is None:
            exists = bool(self.port) and os.path.exists(self.port)
            simulated = config.use_simulation(exists or self._config_error is not None)
        self.simulated = bool(simulated)
        if self.simulated:
            self.transport = SimulatedTransport(name=name, loopback=loopback)
        else:
            self.transport = ZigbeeTransport(port=self.port, name=name, baudrate=9600)
        self._decoder = FrameDecoder()
        self._init_alerts(node_id if node_id is not None else node_id_from_name(name))

    def connect(self) -> bool:
        if not self.simulated and self._config_error:
            return False
        return bool(self.transport.open())

    def disconnect(self) -> None:
        self.transport.close()
        self._decoder.reset()

    def reset_alerts(self) -> None:
        super().reset_alerts()
        self._decoder.reset()

    def is_connected(self) -> bool:
        return bool(self.transport.is_open())

    def send_text(self, dest: str, text: str) -> int:
        return int(self.transport.send_text(dest, text))

    def send_packet(self, dest: str, payload: bytes, portnum: int = 256) -> int:
        return int(self.transport.send_packet(dest, payload, portnum=portnum))

    def poll(self) -> list[dict[str, Any]]:
        return list(self.transport.poll())

    def info(self) -> dict[str, Any]:
        info = self.transport.info()
        info.setdefault("role", "zigbee")
        info["device_type"] = "zigbee"
        info["simulated"] = self.simulated
        info["port"] = self.port
        info["node_id"] = self._node_id
        if self._config_error and not info.get("error"):
            info["error"] = self._config_error
        info["stats"] = self.stats()
        return info

    # -- AlertLinkMixin hooks ---------------------------------------------------------------
    def _send_wire(self, payload: bytes) -> int:
        frame = frame_encode(payload)
        return int(self.transport.send_packet("zigbee", frame))

    def _rx_frames(self, events: list[dict]) -> list[bytes]:
        frames: list[bytes] = []
        for event in events:
            try:
                chunk = bytes.fromhex(event.get("payload", ""))
            except ValueError:
                continue
            frames.extend(self._decoder.feed(chunk))
        return frames
