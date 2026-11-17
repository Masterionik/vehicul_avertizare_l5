"""LoRa Meshtastic link: stock Meshtastic firmware reached over USB serial or TCP (port 4403).

Backend selection (``MCD_SIMULATE``, same rule as the other drivers):

* ``1``    -> simulated transport
* ``0``    -> real device (connect() returns False with an error message if it is missing)
* ``auto`` -> real only if the device exists (serial path present, or a TCP host was given)

Several instances can exist at the same time (the modem plus the extra boards used as simulated
remote vehicles); each has its own name, queue and sequence counter.

Receiving never blocks: the meshtastic library calls back from its own thread and the callback
only puts an event into a queue that ``poll()`` drains.

Alerts are the signed 14-byte packet sent as raw bytes on the PRIVATE_APP port (256).
"""

from __future__ import annotations

import logging
import os
import queue
from typing import Any

from mcd import config

from .alert_link import AlertLinkMixin
from .packets import node_id_from_name
from .transport_sim import SimulatedTransport

log = logging.getLogger(__name__)

ALERT_PORTNUM = 256          # PRIVATE_APP
TEXT_PORTNUM = 1             # TEXT_MESSAGE_APP
_RX_QUEUE_MAX = 256


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


class MeshtasticLink(AlertLinkMixin):
    def __init__(self, *, port: str | None = None, host: str | None = None, portnum: int = 4403,
                 name: str = "meshtastic", node_id: int | None = None,
                 simulated: bool | None = None, loopback: bool = False, modem: bool = False):
        """``modem=True`` selects the Pi's own radio port from MCD config when no port is given."""
        self.name = name
        self.host = host
        self.portnum = int(portnum)
        self._config_error: str | None = None
        self.port = port
        if not host and not port and modem:
            cfg = config.usb_config(config.USB_CH_MESHTASTIC)
            self.port = cfg["port"]
            self._config_error = cfg["error"]
        if simulated is None:
            exists = bool(host) or (bool(self.port) and os.path.exists(self.port)) \
                or self._config_error is not None
            simulated = config.use_simulation(exists)
        self.simulated = bool(simulated)
        self._error: str | None = None
        self._iface = None
        self._rx: queue.Queue = queue.Queue(maxsize=_RX_QUEUE_MAX)
        self._subscribed = False
        self.transport = SimulatedTransport(name=name, loopback=loopback) if self.simulated else None
        self._init_alerts(node_id if node_id is not None else node_id_from_name(name))

    # -- connection ----------------------------------------------------------------------------
    def connect(self) -> bool:
        if self.simulated:
            return bool(self.transport.open())
        if self._iface is not None:
            return True
        if self._config_error:
            self._error = self._config_error
            return False
        if not self.host and not self.port:
            self._error = "no serial port or TCP host configured"
            return False
        try:
            from meshtastic.serial_interface import SerialInterface  # type: ignore
            from meshtastic.tcp_interface import TCPInterface  # type: ignore
            from pubsub import pub  # type: ignore
        except Exception as exc:
            self._error = "meshtastic library not installed (%s)" % exc
            return False
        try:
            if not self._subscribed:
                pub.subscribe(self._on_receive, "meshtastic.receive")
                self._subscribed = True
            if self.host:
                self._iface = TCPInterface(hostname=self.host, portNumber=self.portnum)
            else:
                self._iface = SerialInterface(devPath=self.port)
        except Exception as exc:
            self._iface = None
            self._error = str(exc)
            return False
        self._error = None
        return True

    def disconnect(self) -> None:
        if self.simulated:
            self.transport.close()
            return
        iface, self._iface = self._iface, None
        if iface is not None:
            try:
                iface.close()
            except Exception:
                log.debug("error while closing the meshtastic interface", exc_info=True)
        if self._subscribed:
            try:
                from pubsub import pub  # type: ignore
                pub.unsubscribe(self._on_receive, "meshtastic.receive")
            except Exception:
                pass
            self._subscribed = False

    def is_connected(self) -> bool:
        if self.simulated:
            return bool(self.transport.is_open())
        return self._iface is not None

    # -- receive (library thread -> queue) -------------------------------------------------------
    def _on_receive(self, packet, interface=None):  # pragma: no cover - needs the meshtastic lib
        if interface is not None and interface is not self._iface:
            return
        try:
            decoded = packet.get("decoded") or {}
            portnum = decoded.get("portnum")
            payload = decoded.get("payload") or b""
            if portnum == "PRIVATE_APP":
                num = ALERT_PORTNUM
            elif portnum == "TEXT_MESSAGE_APP":
                num = TEXT_PORTNUM
            else:
                return
            event = {"kind": "rx", "source": self.name, "portnum": num,
                     "payload": bytes(payload).hex(), "from": packet.get("fromId"), "ts": _now()}
            if num == TEXT_PORTNUM:
                event["text"] = decoded.get("text", "")
            self._rx.put_nowait(event)
        except queue.Full:
            log.warning("%s: RX queue full, packet dropped", self.name)
        except Exception:
            log.debug("bad meshtastic packet ignored", exc_info=True)

    def poll(self) -> list[dict[str, Any]]:
        if self.simulated:
            return list(self.transport.poll())
        events = []
        while True:
            try:
                events.append(self._rx.get_nowait())
            except queue.Empty:
                return events

    # -- send ------------------------------------------------------------------------------------
    def send_text(self, dest: str, text: str) -> int:
        if self.simulated:
            return int(self.transport.send_text(dest, text))
        if self._iface is None:
            return 0
        payload = str(text)
        try:
            self._iface.sendText(payload, destinationId=str(dest or "^all"), wantAck=False)
        except Exception as exc:
            self._error = str(exc)
            return 0
        return len(payload.encode("utf-8", errors="replace"))

    def send_packet(self, dest: str, payload: bytes, portnum: int = ALERT_PORTNUM) -> int:
        if self.simulated:
            return int(self.transport.send_packet(dest, payload, portnum=portnum))
        if self._iface is None:
            return 0
        raw = bytes(payload)
        try:
            self._iface.sendData(raw, destinationId=str(dest or "^all"), portNum=int(portnum),
                                 wantAck=False)
        except Exception as exc:
            self._error = str(exc)
            return 0
        return len(raw)

    def info(self) -> dict[str, Any]:
        if self.simulated:
            info = self.transport.info()
        else:
            info = {"id": self.name, "name": self.name,
                    "state": "connected" if self.is_connected() else "disconnected",
                    "transport": "tcp" if self.host else "serial",
                    "connected": self.is_connected(), "error": self._error}
            if self.host:
                info["host"], info["port"] = self.host, self.portnum
            else:
                info["port"] = self.port
        info["simulated"] = self.simulated
        info["role"] = "meshtastic"
        info["device_type"] = "meshtastic"
        info["node_id"] = self._node_id
        if self._config_error and not info.get("error"):
            info["error"] = self._config_error
        info["stats"] = self.stats()
        return info

    # -- AlertLinkMixin hooks --------------------------------------------------------------------
    def _send_wire(self, payload: bytes) -> int:
        return self.send_packet("^all", payload, portnum=ALERT_PORTNUM)

    def _rx_frames(self, events: list[dict]) -> list[bytes]:
        frames = []
        for event in events:
            if event.get("portnum") != ALERT_PORTNUM:
                continue
            try:
                frames.append(bytes.fromhex(event.get("payload", "")))
            except ValueError:
                continue
        return frames
