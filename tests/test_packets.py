"""Tests for the SDC layer: signed alert packet, replay window, Zigbee framing and both links."""
import os
import tempfile
import unittest

from mcd import config
from sdc import (ALERT_LEN, BadSignatureError, FrameDecoder, MeshtasticLink, PacketError, ReplayGuard,
                 ZigbeeLink, decode_alert, encode_alert, frame_encode, node_id_from_name, validate_replay)
from sdc import packets
from sdc.registry import classify_port

NODE = 0x11223344


class _EnvCase(unittest.TestCase):
    """Force simulation and a known HMAC key; restore the environment afterwards."""

    ENV = {"MCD_SIMULATE": "1", "MESH_HMAC_KEY": "test-key"}
    CLEAR = ("MCD_SERIAL_BY_ID_DIR", "ZIGBEE_PORT", "MESHTASTIC_PORT", "MCD_ZIGBEE_PORT", "MCD_MESHTASTIC_PORT")

    def setUp(self):
        saved = {k: os.environ.get(k) for k in list(self.ENV) + list(self.CLEAR)}
        self.addCleanup(self._restore, saved)
        for key in self.CLEAR:
            os.environ.pop(key, None)
        os.environ.update(self.ENV)

    @staticmethod
    def _restore(saved):
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class AlertPacketTests(_EnvCase):
    def test_round_trip(self):
        raw = encode_alert(7, 1, 2, 80, NODE)
        self.assertEqual(decode_alert(raw), {"seq": 7, "kind": 1, "level": 2, "distance_cm": 80, "node": NODE})

    def test_size_is_small(self):
        raw = encode_alert(1, 2, 3, 65535, 0xFFFFFFFF)
        self.assertEqual(len(raw), ALERT_LEN)
        self.assertEqual(len(raw), 14)
        self.assertLess(len(raw), 233)                       # Meshtastic payload limit
        self.assertLess(len(frame_encode(raw)), 233)

    def test_every_tampered_byte_is_rejected(self):
        raw = bytearray(encode_alert(5, 1, 3, 30, NODE))
        for i in range(len(raw)):
            bad = bytearray(raw)
            bad[i] ^= 0x01
            with self.assertRaises(PacketError, msg="byte %d" % i):
                decode_alert(bytes(bad))

    def test_wrong_key_is_rejected(self):
        raw = encode_alert(5, 1, 3, 30, NODE)
        os.environ["MESH_HMAC_KEY"] = "another-key"
        with self.assertRaises(BadSignatureError):
            decode_alert(raw)

    def test_wrong_length_and_type_rejected(self):
        raw = encode_alert(5, 1, 3, 30, NODE)
        for bad in (b"", raw[:-1], raw + b"\x00", None, "text", 123):
            with self.assertRaises(PacketError):
                decode_alert(bad)

    def test_invalid_fields_raise(self):
        cases = [(256, 1, 0, 0, 1), (-1, 1, 0, 0, 1), (0, 0, 0, 0, 1), (0, 3, 0, 0, 1), (0, 1, 4, 0, 1),
                 (0, 1, -1, 0, 1), (0, 1, 0, 70000, 1), (0, 1, 0, -1, 1), (0, 1, 0, 0, 2 ** 32),
                 (True, 1, 0, 0, 1), (0, 1, 0, 1.5, 1), (0, "1", 0, 0, 1)]
        for case in cases:
            with self.assertRaises(PacketError, msg=repr(case)):
                encode_alert(*case)

    def test_boundary_values_accepted(self):
        for args in ((0, 1, 0, 0, 0), (255, 2, 3, 65535, 0xFFFFFFFF)):
            self.assertEqual(decode_alert(encode_alert(*args))["seq"], args[0])

    def test_node_id_from_name_is_stable(self):
        self.assertEqual(node_id_from_name("V2"), node_id_from_name("V2"))
        self.assertNotEqual(node_id_from_name("V2"), node_id_from_name("V3"))

    def test_dev_key_warns_when_env_missing(self):
        os.environ.pop("MESH_HMAC_KEY")
        packets._warned = False
        with self.assertLogs("sdc.packets", level="WARNING"):
            encode_alert(1, 1, 0, 10, 1)


class ReplayTests(unittest.TestCase):
    def test_newer_accepted_equal_and_older_rejected(self):
        self.assertTrue(validate_replay(8, 7))
        self.assertFalse(validate_replay(7, 7))
        self.assertFalse(validate_replay(6, 7))

    def test_first_packet_accepted(self):
        self.assertTrue(validate_replay(200, None))

    def test_wrap_around_accepted(self):
        self.assertTrue(validate_replay(0, 255))
        self.assertTrue(validate_replay(3, 250))
        self.assertFalse(validate_replay(255, 0))        # older across the wrap

    def test_window_edges(self):
        self.assertTrue(validate_replay(127, 0))
        self.assertFalse(validate_replay(128, 0))
        self.assertFalse(validate_replay(200, 0))

    def test_invalid_values_rejected(self):
        for seq, last in ((256, 0), (-1, 0), (True, 0), (1.0, 0), (1, 256), ("1", 0)):
            self.assertFalse(validate_replay(seq, last), (seq, last))

    def test_guard_tracks_nodes_independently(self):
        guard = ReplayGuard()
        self.assertTrue(guard.accept(1, 10))
        self.assertFalse(guard.accept(1, 10))
        self.assertTrue(guard.accept(2, 10))
        self.assertTrue(guard.accept(1, 11))
        guard.reset()
        self.assertTrue(guard.accept(1, 5))


class FramingTests(_EnvCase):
    def setUp(self):
        super().setUp()
        self.pkt = encode_alert(9, 1, 2, 55, NODE)
        self.frame = frame_encode(self.pkt)

    def test_round_trip(self):
        self.assertEqual(FrameDecoder().feed(self.frame), [self.pkt])

    def test_garbage_before_and_between_frames(self):
        dec = FrameDecoder()
        data = b"\x00\x01\x02" + self.frame + b"\xff\xfe" + self.frame
        self.assertEqual(dec.feed(data), [self.pkt, self.pkt])
        self.assertGreater(dec.dropped_bytes, 0)

    def test_split_reads_reassemble(self):
        dec = FrameDecoder()
        out = []
        for i in range(len(self.frame)):                    # one byte at a time
            out += dec.feed(self.frame[i:i + 1])
        self.assertEqual(out, [self.pkt])

    def test_resync_after_false_sync_with_bogus_length(self):
        dec = FrameDecoder()
        data = b"\x7e\xa5\x0e" + b"\x01\x02\x03" + self.frame     # false header swallows a few bytes
        out = dec.feed(data) + dec.feed(self.frame)
        self.assertIn(self.pkt, out)

    def test_corrupted_crc_is_dropped_then_next_frame_decodes(self):
        bad = bytearray(self.frame)
        bad[-1] ^= 0xFF
        self.assertEqual(FrameDecoder().feed(bytes(bad) + self.frame), [self.pkt])

    def test_pure_garbage_yields_nothing_and_bounded_buffer(self):
        dec = FrameDecoder()
        self.assertEqual(dec.feed(os.urandom(2000).replace(b"\x7e", b"\x00")), [])
        self.assertLessEqual(len(dec._buf), 256)

    def test_invalid_payload_sizes_rejected(self):
        for payload in (b"", b"x" * 256):
            with self.assertRaises(PacketError):
                frame_encode(payload)


class _LinkTests:
    """Same contract for both radios (mixed into one TestCase per link)."""

    def make(self, **kw):
        raise NotImplementedError

    def wire(self, raw):
        """How a received alert looks on the transport (Zigbee frames it, Meshtastic does not)."""
        return raw

    def inject(self, link, wire):
        link.transport.inject_rx(wire)

    def test_loopback_send_receive(self):
        link = self.make(loopback=True)
        self.assertTrue(link.connect())
        self.assertGreater(link.send_alert(1, 2, 80), 0)
        alert = link.recv_alert()
        self.assertEqual((alert["kind"], alert["level"], alert["distance_cm"]), (1, 2, 80))
        self.assertEqual(alert["node"], link.node_id)
        self.assertIsNone(link.recv_alert())

    def test_sent_packet_is_decodable(self):
        link = self.make()
        link.connect()
        sent = link.send_alert(2, 3, 12)
        raw = link.transport.sent_packets[-1]
        self.assertEqual(len(raw), sent)
        payload = FrameDecoder().feed(raw)[0] if raw[:2] == packets.SYNC else raw
        decoded = decode_alert(payload)
        self.assertEqual((decoded["kind"], decoded["level"], decoded["distance_cm"]), (2, 3, 12))

    def test_sequence_increments(self):
        link = self.make(loopback=True)
        link.connect()
        for _ in range(3):
            link.send_alert(1, 1, 100)
        self.assertEqual([link.recv_alert()["seq"] for _ in range(3)], [1, 2, 3])

    def test_invalid_args_return_zero(self):
        link = self.make()
        link.connect()
        for args in ((0, 1, 10), (3, 1, 10), (1, 4, 10), (1, -1, 10), (1, 1, 70000), (1, 1, -1), (None, 1, 1)):
            self.assertEqual(link.send_alert(*args), 0, args)
        self.assertEqual(len(link.transport.sent_packets), 0)

    def test_not_connected_returns_zero(self):
        link = self.make()
        self.assertFalse(link.is_connected())
        self.assertEqual(link.send_alert(1, 1, 10), 0)
        link.connect()
        link.disconnect()
        self.assertEqual(link.send_alert(1, 1, 10), 0)

    def test_tampered_packet_is_dropped(self):
        link = self.make()
        link.connect()
        raw = bytearray(encode_alert(1, 1, 2, 40, NODE))
        raw[4] ^= 0x10
        self.inject(link, self.wire(bytes(raw)))
        self.assertIsNone(link.recv_alert())
        self.assertEqual(link.stats()["rx_bad_signature"], 1)

    def test_wrong_key_packet_is_dropped(self):
        link = self.make()
        link.connect()
        os.environ["MESH_HMAC_KEY"] = "attacker"
        raw = encode_alert(1, 1, 2, 40, NODE)
        os.environ["MESH_HMAC_KEY"] = "test-key"
        self.inject(link, self.wire(raw))
        self.assertIsNone(link.recv_alert())

    def test_replayed_packet_is_dropped(self):
        link = self.make()
        link.connect()
        wire = self.wire(encode_alert(10, 1, 2, 40, NODE))
        self.inject(link, wire)
        self.assertEqual(link.recv_alert()["seq"], 10)
        self.inject(link, wire)                               # same packet again
        self.assertIsNone(link.recv_alert())
        self.inject(link, self.wire(encode_alert(9, 1, 2, 40, NODE)))   # older
        self.assertIsNone(link.recv_alert())
        self.assertEqual(link.stats()["rx_replay"], 2)
        self.inject(link, self.wire(encode_alert(11, 1, 2, 41, NODE)))
        self.assertEqual(link.recv_alert()["distance_cm"], 41)

    def test_wrap_around_packet_is_accepted(self):
        link = self.make()
        link.connect()
        self.inject(link, self.wire(encode_alert(255, 1, 1, 100, NODE)))
        self.inject(link, self.wire(encode_alert(0, 1, 1, 90, NODE)))
        self.assertEqual([link.recv_alert()["seq"], link.recv_alert()["seq"]], [255, 0])

    def test_alerts_are_returned_oldest_first(self):
        link = self.make()
        link.connect()
        for seq, dist in ((1, 100), (2, 90), (3, 80)):
            self.inject(link, self.wire(encode_alert(seq, 1, 1, dist, NODE)))
        self.assertEqual([link.recv_alert()["distance_cm"] for _ in range(3)], [100, 90, 80])

    def test_info_reports_simulated(self):
        link = self.make()
        self.assertTrue(link.info()["simulated"])
        self.assertEqual(link.info()["state"], "disconnected")
        link.connect()
        self.assertEqual(link.info()["state"], "connected")

    def test_forced_real_without_device_fails_cleanly(self):
        os.environ["MCD_SIMULATE"] = "0"
        link = self.make(port="/dev/does-not-exist-xyz")
        self.assertFalse(link.info()["simulated"])
        self.assertFalse(link.connect())
        self.assertFalse(link.is_connected())
        self.assertEqual(link.send_alert(1, 1, 10), 0)

    def test_auto_without_device_is_simulated(self):
        os.environ["MCD_SIMULATE"] = "auto"
        self.assertTrue(self.make(port="/dev/does-not-exist-xyz").info()["simulated"])

    def test_reset_alerts_clears_state(self):
        link = self.make(loopback=True)
        link.connect()
        link.send_alert(1, 1, 10)
        link.reset_alerts()
        self.assertIsNone(link.recv_alert())
        self.assertEqual(link.stats()["tx"], 0)


class MeshtasticLinkTests(_LinkTests, _EnvCase):
    def make(self, **kw):
        return MeshtasticLink(name="mesh-test", **kw)

    def test_text_messages_do_not_become_alerts(self):
        link = self.make(loopback=True)
        link.connect()
        self.assertEqual(link.send_text("^all", "hello"), 5)
        self.assertIsNone(link.recv_alert())
        self.assertEqual(link.stats()["rx_bad_format"], 0)

    def test_several_instances_are_independent(self):
        a, b = self.make(loopback=True), MeshtasticLink(name="other", loopback=True)
        a.connect(), b.connect()
        a.send_alert(1, 1, 50)
        self.assertIsNone(b.recv_alert())
        self.assertIsNotNone(a.recv_alert())
        self.assertNotEqual(a.node_id, b.node_id)

    def test_modem_ambiguity_requires_env_variable(self):
        with tempfile.TemporaryDirectory() as d:
            for name in ("usb-Silicon_Labs_CP2102_0001-if00-port0", "usb-Silicon_Labs_CP2102_0002-if00-port0"):
                open(os.path.join(d, name), "w").close()
            os.environ["MCD_SERIAL_BY_ID_DIR"] = d
            os.environ["MCD_SIMULATE"] = "0"
            link = MeshtasticLink(modem=True)
            self.assertFalse(link.connect())
            self.assertIn("MESHTASTIC_PORT", link.info()["error"])
            os.environ["MESHTASTIC_PORT"] = "/dev/does-not-exist-xyz"
            self.assertEqual(MeshtasticLink(modem=True).port, "/dev/does-not-exist-xyz")


class ZigbeeLinkTests(_LinkTests, _EnvCase):
    def make(self, **kw):
        return ZigbeeLink(name="zb-test", **kw)

    def wire(self, raw):
        return frame_encode(raw)

    def test_frame_split_across_reads(self):
        link = self.make()
        link.connect()
        wire = frame_encode(encode_alert(4, 1, 3, 25, NODE))
        link.transport.inject_rx(wire[:5])
        self.assertIsNone(link.recv_alert())
        link.transport.inject_rx(wire[5:])
        self.assertEqual(link.recv_alert()["level"], 3)

    def test_garbage_then_valid_frame(self):
        link = self.make()
        link.connect()
        link.transport.inject_rx(b"\x00\x7e\x13\xfa" + frame_encode(encode_alert(4, 1, 3, 25, NODE)))
        self.assertEqual(link.recv_alert()["distance_cm"], 25)

    def test_xbee_ambiguity_requires_env_variable(self):
        with tempfile.TemporaryDirectory() as d:
            for name in ("usb-FTDI_FT232R_USB_UART_A-if00-port0", "usb-FTDI_FT232R_USB_UART_B-if00-port0"):
                open(os.path.join(d, name), "w").close()
            os.environ["MCD_SERIAL_BY_ID_DIR"] = d
            os.environ["MCD_SIMULATE"] = "0"
            link = ZigbeeLink()
            self.assertFalse(link.connect())
            self.assertIn("ZIGBEE_PORT", link.info()["error"])


class RegistryClassificationTests(unittest.TestCase):
    def test_classify_by_vendor_strings(self):
        self.assertEqual(classify_port("/dev/ttyUSB0", by_id="usb-Silicon_Labs_CP2102_0001-if00-port0"), "meshtastic")
        self.assertEqual(classify_port("/dev/ttyUSB1", by_id="usb-FTDI_FT232R_USB_UART_A-if00-port0"), "xbee")
        self.assertEqual(classify_port("/dev/ttyACM0", description="Heltec Wireless Stick Lite V3"), "meshtastic")
        self.assertEqual(classify_port("/dev/ttyUSB0", description="USB Serial Device", vid="0x0403"), "xbee")
        self.assertEqual(classify_port("/dev/ttyUSB2", description="Some adapter"), "unknown")


if __name__ == "__main__":
    unittest.main()
