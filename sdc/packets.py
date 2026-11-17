"""Signed alert packet shared by both radios (LoRa Meshtastic and Zigbee).

Byte layout (14 bytes, little endian, one format for both radios)::

    offset  size  field
    0       1     type          0x01 = alert
    1       1     seq           8-bit sequence number (wraps 255 -> 0)
    2       1     kind          1 = proximity, 2 = impact
    3       1     level         0 clear, 1 caution, 2 warning, 3 danger
    4       2     distance_cm   0..65535 (u16)
    6       4     node_id       sender id (u32)
    10      4     hmac          HMAC-SHA256 over bytes 0..9, truncated to 4 bytes

Trade-off: a 4-byte tag keeps the frame tiny (LoRa airtime is scarce) but gives only 32 bits of
forgery resistance, which is fine for a warning-only demo and must not be reused for anything
security critical. Replay protection is done by the receiver with ``validate_replay`` /
``ReplayGuard`` (8-bit sequence numbers with a wrap-around window).

Zigbee is a byte stream (XBee transparent mode), so over Zigbee the packet is wrapped in a frame::

    0x7E 0xA5 | length(1) | payload | crc8(1)

``FrameDecoder`` resynchronizes after garbage or partial reads. The CRC only detects line noise
and misalignment; authenticity is still decided by the HMAC.

The key comes from the environment variable ``MESH_HMAC_KEY``. A development default exists but
logs a warning once.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import struct
import threading
import zlib

log = logging.getLogger(__name__)

KEY_ENV = "MESH_HMAC_KEY"
_DEV_KEY = b"proximity-warning-dev-key"

PACKET_TYPE_ALERT = 0x01
KIND_PROXIMITY = 1
KIND_IMPACT = 2
LEVEL_MAX = 3
DISTANCE_MAX = 0xFFFF
NODE_ID_MAX = 0xFFFFFFFF

MAC_LEN = 4
_BODY_FMT = "<BBBBHI"
BODY_LEN = struct.calcsize(_BODY_FMT)          # 10
ALERT_LEN = BODY_LEN + MAC_LEN                 # 14

SYNC = b"\x7e\xa5"
FRAME_OVERHEAD = len(SYNC) + 1 + 1             # sync + length + crc
FRAME_LEN = ALERT_LEN + FRAME_OVERHEAD         # 18
_MAX_BUFFER = 256

_warn_lock = threading.Lock()
_warned = False


class PacketError(ValueError):
    """Raised when a packet is malformed, has invalid fields or a bad signature."""


class BadSignatureError(PacketError):
    """The HMAC tag does not match (tampered packet or wrong key)."""


def _key() -> bytes:
    global _warned
    value = os.environ.get(KEY_ENV, "")
    if value:
        return value.encode("utf-8")
    with _warn_lock:
        if not _warned:
            _warned = True
            log.warning("%s is not set: using the insecure development HMAC key", KEY_ENV)
    return _DEV_KEY


def _mac(body: bytes) -> bytes:
    return hmac.new(_key(), body, hashlib.sha256).digest()[:MAC_LEN]


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def encode_alert(seq, kind, level, distance_cm, node_id) -> bytes:
    """Build a signed alert packet (14 bytes). Raises PacketError for invalid fields."""
    if not _is_int(seq) or not 0 <= seq <= 0xFF:
        raise PacketError("seq out of range: %r" % (seq,))
    if not _is_int(kind) or kind not in (KIND_PROXIMITY, KIND_IMPACT):
        raise PacketError("kind must be 1 (proximity) or 2 (impact): %r" % (kind,))
    if not _is_int(level) or not 0 <= level <= LEVEL_MAX:
        raise PacketError("level must be 0..3: %r" % (level,))
    if not _is_int(distance_cm) or not 0 <= distance_cm <= DISTANCE_MAX:
        raise PacketError("distance_cm must be 0..65535: %r" % (distance_cm,))
    if not _is_int(node_id) or not 0 <= node_id <= NODE_ID_MAX:
        raise PacketError("node_id must be a u32: %r" % (node_id,))
    body = struct.pack(_BODY_FMT, PACKET_TYPE_ALERT, seq, kind, level, distance_cm, node_id)
    return body + _mac(body)


def decode_alert(packet) -> dict:
    """Verify and parse an alert packet.

    Returns ``{"seq", "kind", "level", "distance_cm", "node"}`` or raises PacketError
    (BadSignatureError for a wrong tag).
    """
    if not isinstance(packet, (bytes, bytearray, memoryview)):
        raise PacketError("packet must be bytes")
    raw = bytes(packet)
    if len(raw) != ALERT_LEN:
        raise PacketError("bad packet length: %d (expected %d)" % (len(raw), ALERT_LEN))
    body, tag = raw[:BODY_LEN], raw[BODY_LEN:]
    if not hmac.compare_digest(tag, _mac(body)):
        raise BadSignatureError("invalid HMAC signature")
    ptype, seq, kind, level, distance_cm, node = struct.unpack(_BODY_FMT, body)
    if ptype != PACKET_TYPE_ALERT:
        raise PacketError("unsupported packet type: %d" % ptype)
    if kind not in (KIND_PROXIMITY, KIND_IMPACT) or level > LEVEL_MAX:
        raise PacketError("invalid kind/level in a correctly signed packet")
    return {"seq": seq, "kind": kind, "level": level, "distance_cm": distance_cm, "node": node}


def node_id_from_name(name: str) -> int:
    """Stable u32 node id derived from a short name such as "V2" (CRC32)."""
    return zlib.crc32(str(name).encode("utf-8")) & NODE_ID_MAX


# ------------------------------------------------------------------ replay protection
REPLAY_WINDOW = 128


def validate_replay(seq, last_seq) -> bool:
    """True only if ``seq`` is newer than ``last_seq`` (8-bit wrap-around, window of 128).

    The forward distance (seq - last_seq) mod 256 must be 1..127: equal and older sequence
    numbers are rejected, 255 -> 0 is accepted as newer. ``last_seq=None`` means "nothing seen".
    """
    if not _is_int(seq) or not 0 <= seq <= 0xFF:
        return False
    if last_seq is None:
        return True
    if not _is_int(last_seq) or not 0 <= last_seq <= 0xFF:
        return False
    diff = (seq - last_seq) & 0xFF
    return 1 <= diff < REPLAY_WINDOW


class ReplayGuard:
    """Remembers the last accepted sequence number per sender node."""

    def __init__(self):
        self._last: dict[int, int] = {}
        self._lock = threading.Lock()

    def accept(self, node, seq) -> bool:
        with self._lock:
            if not validate_replay(seq, self._last.get(node)):
                return False
            self._last[node] = seq
            return True

    def reset(self) -> None:
        with self._lock:
            self._last.clear()


# ------------------------------------------------------------------ Zigbee framing
def crc8(data: bytes) -> int:
    """CRC-8 (polynomial 0x07)."""
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def frame_encode(payload) -> bytes:
    """Wrap a payload (1..255 bytes) in sync + length + payload + crc8."""
    raw = bytes(payload)
    if not 1 <= len(raw) <= 255:
        raise PacketError("frame payload must be 1..255 bytes")
    return SYNC + bytes([len(raw)]) + raw + bytes([crc8(raw)])


class FrameDecoder:
    """Stream decoder: feed arbitrary chunks, get complete payloads; resyncs after garbage."""

    def __init__(self, max_payload: int = ALERT_LEN):
        self._buf = bytearray()
        self._max = max_payload
        self.dropped_bytes = 0

    def reset(self) -> None:
        self._buf.clear()

    def feed(self, data) -> list[bytes]:
        self._buf.extend(bytes(data))
        out: list[bytes] = []
        buf = self._buf
        while True:
            start = buf.find(SYNC)
            if start < 0:
                # keep a possible first sync byte at the end, drop the rest
                keep = 1 if buf and buf[-1] == SYNC[0] else 0
                self.dropped_bytes += len(buf) - keep
                del buf[:len(buf) - keep]
                break
            if start:
                self.dropped_bytes += start
                del buf[:start]
            if len(buf) < 3:
                break
            length = buf[2]
            if length < 1 or length > self._max:
                self.dropped_bytes += 1
                del buf[:1]
                continue
            total = 3 + length + 1
            if len(buf) < total:
                break
            payload = bytes(buf[3:3 + length])
            if buf[3 + length] == crc8(payload):
                out.append(payload)
                del buf[:total]
            else:
                self.dropped_bytes += 1
                del buf[:1]
        if len(buf) > _MAX_BUFFER:
            self.dropped_bytes += len(buf) - _MAX_BUFFER
            del buf[:len(buf) - _MAX_BUFFER]
        return out
