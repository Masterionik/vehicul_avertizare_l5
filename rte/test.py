"""RTE unit tests (lab L5: the equivalent of ``unit.c``).

Every public RTE function is tested, one test class per function group:

    LifecycleTests       RTE_vInit, RTE_vDeinit
    BuzzerLedTests       RTE_vSetBuzzer, RTE_vSetLed
    DistanceTests        RTE_u16GetDistanceCm, RTE_u8GetDistanceSensorStatus
    VibrationTests       RTE_u16GetVibrationEdges, RTE_u8GetVibrationState
    TickTests            RTE_u8GetTick100ms, RTE_u8GetTick1s, RTE_u8GetTick60s
    MeshtasticTests      RTE_u8GetMeshStatus, RTE_u16SendMeshAlert, RTE_u16SendMeshText, RTE_tGetMeshAlert
    ZigbeeTests          RTE_u8GetZigbeeStatus, RTE_u16SendZigbeeAlert, RTE_tGetZigbeeAlert
    SignalTests          RTE_vWrite* / RTE_u*Read* (virtual functional bus)
    DocumentationTests   docs/rte_api.md lists exactly the public RTE functions

Run with ``MCD_SIMULATE=1 python -m rte.test``; also discovered by ``manage.py test``.
The tests force MCD_SIMULATE=1 and restore the environment afterwards.
"""
import inspect
import os
import re
import threading
import unittest

import rte
from mcd import config, pcs, tim, ultrasonic, usb
from sdc import FrameDecoder, MeshtasticLink, ZigbeeLink, decode_alert, encode_alert, frame_encode
from sdc.packets import SYNC

BAD_BINARY = (2, -1, 0.6, 1.0, True, False, None, "1", [1])
NODE = 0x0A0B0C0D
_ENV = {"MCD_SIMULATE": "1", "MESH_HMAC_KEY": "rte-test-key", "MESH_NODE_ID": "0x01020304"}
_CLEAR = ("ZIGBEE_PORT", "MESHTASTIC_PORT", "MCD_ZIGBEE_PORT", "MCD_MESHTASTIC_PORT", "MCD_SERIAL_BY_ID_DIR")


class FakeClock:
    """Controllable clock (seconds) for the tick tests."""

    def __init__(self, t=0.0):
        self.t = t

    def __call__(self):
        return self.t


class _RteTestCase(unittest.TestCase):
    """Simulation mode, known keys, clean drivers before and after every test."""

    @staticmethod
    def _reset():
        rte.RTE_vDeinit()
        ultrasonic.US_vDeinit()
        pcs.PCS_vDeinit()
        usb.USB_vCloseAll()
        tim.TIM_vInit()

    def setUp(self):
        saved = {k: os.environ.get(k) for k in list(_ENV) + list(_CLEAR)}

        def restore():
            self._reset()
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

        self.addCleanup(restore)
        for key in _CLEAR:
            os.environ.pop(key, None)
        os.environ.update(_ENV)
        self._reset()


# ------------------------------------------------------------------------------ lifecycle
class LifecycleTests(_RteTestCase):
    def test_init_leaves_outputs_off_and_links_up(self):
        rte.RTE_vInit()
        self.assertEqual(pcs.PCS_u8GetGpio(config.GPIO_BUZZER), 0)
        self.assertEqual(pcs.PCS_u8GetGpio(config.GPIO_LED), 0)
        self.assertEqual(rte.RTE_u8GetMeshStatus(), 1)
        self.assertEqual(rte.RTE_u8GetZigbeeStatus(), 1)

    def test_init_twice_is_safe_and_keeps_the_same_links(self):
        rte.RTE_vInit()
        mesh, zigbee = rte._mesh, rte._zigbee
        rte.RTE_vWriteAlertLevel(2)
        rte.RTE_vInit()
        self.assertIs(rte._mesh, mesh)
        self.assertIs(rte._zigbee, zigbee)
        self.assertEqual(rte.RTE_u8ReadAlertLevel(), 2)           # a second init does not reset signals
        self.assertEqual(rte.RTE_u8GetMeshStatus(), 1)

    def test_deinit_forces_buzzer_and_led_off(self):
        rte.RTE_vInit()
        rte.RTE_vSetBuzzer(1)
        rte.RTE_vSetLed(1)
        self.assertEqual(pcs.PCS_u8GetGpio(config.GPIO_BUZZER), 1)
        self.assertEqual(pcs.PCS_u8GetGpio(config.GPIO_LED), 1)
        rte.RTE_vDeinit()
        self.assertEqual(pcs.PCS_u8GetGpio(config.GPIO_BUZZER), 0)
        self.assertEqual(pcs.PCS_u8GetGpio(config.GPIO_LED), 0)

    def test_deinit_closes_links_stops_timers_and_resets_signals(self):
        clk = FakeClock()
        tim.TIM_vSetClock(clk)
        rte.RTE_vInit()
        rte.RTE_vWriteDistanceCm(120)
        rte.RTE_vWriteAlertLevel(3)
        rte.RTE_vWriteRemoteAlertLevel(2)
        rte.RTE_vWriteImpact(1)
        rte.RTE_vDeinit()
        self.assertEqual(rte.RTE_u8GetMeshStatus(), 0)
        self.assertEqual(rte.RTE_u8GetZigbeeStatus(), 0)
        self.assertEqual(rte.RTE_u16ReadDistanceCm(), rte.RTE_DISTANCE_INVALID)
        self.assertEqual(rte.RTE_u8ReadAlertLevel(), 0)
        self.assertEqual(rte.RTE_u8ReadRemoteAlertLevel(), 0)
        self.assertEqual(rte.RTE_u8ReadImpact(), 0)
        clk.t = 100.0
        self.assertEqual(rte.RTE_u8GetTick100ms(), 0)             # timers stopped

    def test_deinit_without_init_and_twice_is_safe(self):
        rte.RTE_vDeinit()
        rte.RTE_vDeinit()
        self.assertEqual(pcs.PCS_u8GetGpio(config.GPIO_BUZZER), 0)

    def test_reinit_after_deinit_works(self):
        rte.RTE_vInit()
        rte.RTE_vDeinit()
        rte.RTE_vInit()
        self.assertEqual(rte.RTE_u8GetMeshStatus(), 1)
        rte.RTE_vSetBuzzer(1)
        self.assertEqual(pcs.PCS_u8GetGpio(config.GPIO_BUZZER), 1)

    def test_deinit_turns_outputs_off_even_if_a_link_fails_to_close(self):
        rte.RTE_vInit()
        rte.RTE_vSetBuzzer(1)

        def boom():
            raise RuntimeError("close failed")

        rte._mesh.disconnect = boom
        rte.RTE_vDeinit()
        self.assertEqual(pcs.PCS_u8GetGpio(config.GPIO_BUZZER), 0)


# ---------------------------------------------------------------------- buzzer and LED
class BuzzerLedTests(_RteTestCase):
    def test_buzzer_on_off_drives_gpio17(self):
        rte.RTE_vInit()
        rte.RTE_vSetBuzzer(1)
        self.assertEqual(pcs.PCS_u8GetGpio(17), 1)
        self.assertEqual(pcs.PCS_u8GetGpio(27), 0)                # the LED is not affected
        rte.RTE_vSetBuzzer(0)
        self.assertEqual(pcs.PCS_u8GetGpio(17), 0)

    def test_led_on_off_drives_gpio27(self):
        rte.RTE_vInit()
        rte.RTE_vSetLed(1)
        self.assertEqual(pcs.PCS_u8GetGpio(27), 1)
        self.assertEqual(pcs.PCS_u8GetGpio(17), 0)                # the buzzer is not affected
        rte.RTE_vSetLed(0)
        self.assertEqual(pcs.PCS_u8GetGpio(27), 0)

    def test_invalid_buzzer_values_leave_the_pin_unchanged(self):
        rte.RTE_vInit()
        for start in (0, 1):
            rte.RTE_vSetBuzzer(start)
            for bad in BAD_BINARY:
                rte.RTE_vSetBuzzer(bad)
                self.assertEqual(pcs.PCS_u8GetGpio(17), start, "start=%r bad=%r" % (start, bad))

    def test_invalid_led_values_leave_the_pin_unchanged(self):
        rte.RTE_vInit()
        for start in (0, 1):
            rte.RTE_vSetLed(start)
            for bad in BAD_BINARY:
                rte.RTE_vSetLed(bad)
                self.assertEqual(pcs.PCS_u8GetGpio(27), start, "start=%r bad=%r" % (start, bad))

    def test_setters_return_none(self):
        rte.RTE_vInit()
        self.assertIsNone(rte.RTE_vSetBuzzer(1))
        self.assertIsNone(rte.RTE_vSetLed(1))
        self.assertIsNone(rte.RTE_vSetBuzzer(5))


# ------------------------------------------------------------------------------ distance
class DistanceTests(_RteTestCase):
    def setUp(self):
        super().setUp()
        rte.RTE_vInit()

    def test_valid_distance_is_returned_and_status_is_one(self):
        ultrasonic.US_vSimSetDistanceCm(100)
        self.assertEqual(rte.RTE_u16GetDistanceCm(), 100)
        self.assertEqual(rte.RTE_u8GetDistanceSensorStatus(), 1)

    def test_range_boundaries_are_valid(self):
        for cm in (2, 400):
            ultrasonic.US_vSimSetDistanceCm(cm)
            self.assertEqual(rte.RTE_u16GetDistanceCm(), cm)
            self.assertEqual(rte.RTE_u8GetDistanceSensorStatus(), 1)

    def test_out_of_range_is_invalid(self):
        for cm in (500, 401, 1, 0):
            ultrasonic.US_vSimSetDistanceCm(cm)
            self.assertEqual(rte.RTE_u16GetDistanceCm(), rte.RTE_DISTANCE_INVALID, cm)
            self.assertEqual(rte.RTE_u8GetDistanceSensorStatus(), 0, cm)

    def test_no_echo_is_invalid(self):
        ultrasonic.US_vSimSetDistanceCm(None)
        self.assertEqual(rte.RTE_u16GetDistanceCm(), 0xFFFF)
        self.assertEqual(rte.RTE_u8GetDistanceSensorStatus(), 0)

    def test_status_follows_the_last_measurement(self):
        ultrasonic.US_vSimSetDistanceCm(150)
        rte.RTE_u16GetDistanceCm()
        self.assertEqual(rte.RTE_u8GetDistanceSensorStatus(), 1)
        ultrasonic.US_vSimSetDistanceCm(None)
        rte.RTE_u16GetDistanceCm()
        self.assertEqual(rte.RTE_u8GetDistanceSensorStatus(), 0)
        ultrasonic.US_vSimSetDistanceCm(150)
        rte.RTE_u16GetDistanceCm()
        self.assertEqual(rte.RTE_u8GetDistanceSensorStatus(), 1)

    def test_invalid_constant_is_0xffff(self):
        self.assertEqual(rte.RTE_DISTANCE_INVALID, 0xFFFF)


# ----------------------------------------------------------------------------- vibration
class VibrationTests(_RteTestCase):
    def setUp(self):
        super().setUp()
        rte.RTE_vInit()
        self.pin = config.GPIO_VIB[0]

    def test_edges_are_returned_once_then_cleared(self):
        pcs.PCS_vSimInjectEdges(self.pin, 4)
        self.assertEqual(rte.RTE_u16GetVibrationEdges(), 4)
        self.assertEqual(rte.RTE_u16GetVibrationEdges(), 0)

    def test_no_edges_at_rest(self):
        self.assertEqual(rte.RTE_u16GetVibrationEdges(), 0)

    def test_edges_accumulate_between_calls(self):
        pcs.PCS_vSimInjectEdges(self.pin, 2)
        pcs.PCS_vSimInjectEdges(self.pin, 3)
        self.assertEqual(rte.RTE_u16GetVibrationEdges(), 5)

    def test_edges_of_all_vibration_inputs_are_summed(self):
        pins = config.vibration_pins()
        for pin in pins:
            pcs.PCS_vSimInjectEdges(pin, 2)
        self.assertEqual(rte.RTE_u16GetVibrationEdges(), 2 * len(pins))

    def test_state_reflects_the_pin_level(self):
        self.assertEqual(rte.RTE_u8GetVibrationState(), 0)
        pcs.PCS_vSimSetInput(self.pin, 1)
        self.assertEqual(rte.RTE_u8GetVibrationState(), 1)
        pcs.PCS_vSimSetInput(self.pin, 0)
        self.assertEqual(rte.RTE_u8GetVibrationState(), 0)

    def test_state_is_binary(self):
        pcs.PCS_vSimSetInput(self.pin, 1)
        self.assertIn(rte.RTE_u8GetVibrationState(), (0, 1))


# ---------------------------------------------------------------------------------- ticks
class TickTests(_RteTestCase):
    def setUp(self):
        super().setUp()
        self.clk = FakeClock(0.0)
        tim.TIM_vSetClock(self.clk)

    def test_no_ticks_before_init(self):
        self.clk.t = 1000.0
        self.assertEqual((rte.RTE_u8GetTick100ms(), rte.RTE_u8GetTick1s(), rte.RTE_u8GetTick60s()), (0, 0, 0))

    def test_100ms_tick_once_per_period(self):
        rte.RTE_vInit()
        self.clk.t = 0.099
        self.assertEqual(rte.RTE_u8GetTick100ms(), 0)
        self.clk.t = 0.1
        self.assertEqual(rte.RTE_u8GetTick100ms(), 1)
        self.assertEqual(rte.RTE_u8GetTick100ms(), 0)             # consumed
        self.clk.t = 0.2
        self.assertEqual(rte.RTE_u8GetTick100ms(), 1)

    def test_1s_tick_once_per_period(self):
        rte.RTE_vInit()
        self.clk.t = 0.999
        self.assertEqual(rte.RTE_u8GetTick1s(), 0)
        self.clk.t = 1.0
        self.assertEqual(rte.RTE_u8GetTick1s(), 1)
        self.assertEqual(rte.RTE_u8GetTick1s(), 0)
        self.clk.t = 2.0
        self.assertEqual(rte.RTE_u8GetTick1s(), 1)

    def test_60s_tick_once_per_period(self):
        rte.RTE_vInit()
        self.clk.t = 59.9
        self.assertEqual(rte.RTE_u8GetTick60s(), 0)
        self.clk.t = 60.0
        self.assertEqual(rte.RTE_u8GetTick60s(), 1)
        self.assertEqual(rte.RTE_u8GetTick60s(), 0)

    def test_ticks_are_independent(self):
        rte.RTE_vInit()
        self.clk.t = 1.0
        self.assertEqual(rte.RTE_u8GetTick1s(), 1)
        self.assertEqual(rte.RTE_u8GetTick100ms(), 1)             # not consumed by the 1 s read
        self.assertEqual(rte.RTE_u8GetTick60s(), 0)

    def test_missed_periods_do_not_burst(self):
        rte.RTE_vInit()
        self.clk.t = 5.55
        self.assertEqual(rte.RTE_u8GetTick100ms(), 1)
        self.assertEqual(rte.RTE_u8GetTick100ms(), 0)


# ---------------------------------------------------------------------------- radio links
class _RadioTests:
    """Same tests for both radios; the subclass says which RTE functions and link to use."""

    status = send = get = None          # RTE functions (set by the subclass as staticmethods)
    link_name = ""
    framed = False                      # True when the wire format is the Zigbee frame

    def link(self):
        return getattr(rte, self.link_name)

    def wire(self, raw):
        return frame_encode(raw) if self.framed else raw

    def inject(self, raw):
        self.link().transport.inject_rx(self.wire(raw))

    def captured(self):
        """Decoded dict of the last packet the transport transmitted."""
        raw = self.link().transport.sent_packets[-1]
        payload = FrameDecoder().feed(raw)[0] if raw[:2] == SYNC else raw
        return decode_alert(payload), raw

    def test_status_is_zero_before_init_and_one_after(self):
        self.assertEqual(self.status(), 0)
        rte.RTE_vInit()
        self.assertEqual(self.status(), 1)
        rte.RTE_vDeinit()
        self.assertEqual(self.status(), 0)

    def test_nothing_works_before_init(self):
        self.assertEqual(self.send(1, 2, 50), 0)
        self.assertIsNone(self.get())

    def test_send_returns_bytes_and_transport_captures_a_decodable_packet(self):
        rte.RTE_vInit()
        sent = self.send(1, 2, 80)
        self.assertGreater(sent, 0)
        decoded, raw = self.captured()
        self.assertEqual(len(raw), sent)
        self.assertEqual((decoded["kind"], decoded["level"], decoded["distance_cm"]), (1, 2, 80))
        self.assertEqual(decoded["node"], 0x01020304)             # from MESH_NODE_ID

    def test_impact_kind_and_boundaries_are_accepted(self):
        rte.RTE_vInit()
        for kind, level, dist in ((2, 3, 0), (1, 0, 65535), (2, 1, 400)):
            self.assertGreater(self.send(kind, level, dist), 0, (kind, level, dist))
            decoded, _ = self.captured()
            self.assertEqual((decoded["kind"], decoded["level"], decoded["distance_cm"]), (kind, level, dist))

    def test_invalid_arguments_return_zero_and_send_nothing(self):
        rte.RTE_vInit()
        bad_calls = [(0, 1, 10), (3, 1, 10), (-1, 1, 10), (1, 4, 10), (1, -1, 10), (1, 1, 70000), (1, 1, -1),
                     (True, 1, 10), (1, True, 10), (1, 1, True), (1.0, 1, 10), (1, 1.0, 10), (1, 1, 10.5),
                     (None, 1, 10), ("1", 1, 10), (1, "1", 10), (1, 1, "10")]
        for args in bad_calls:
            self.assertEqual(self.send(*args), 0, args)
        self.assertEqual(len(self.link().transport.sent_packets), 0)

    def test_loopback_of_a_sent_packet_is_received(self):
        rte.RTE_vInit()
        self.send(2, 3, 25)
        _, raw = self.captured()
        self.link().transport.inject_rx(raw)                      # what was sent is received
        alert = self.get()
        self.assertEqual(alert["kind"], 2)
        self.assertEqual(alert["level"], 3)
        self.assertEqual(alert["distance_cm"], 25)
        self.assertEqual(set(alert), {"node", "kind", "level", "distance_cm", "seq"})
        self.assertIsNone(self.get())

    def test_get_returns_none_when_nothing_was_received(self):
        rte.RTE_vInit()
        self.assertIsNone(self.get())

    def test_received_alert_from_another_node(self):
        rte.RTE_vInit()
        self.inject(encode_alert(5, 1, 2, 40, NODE))
        self.assertEqual(self.get(), {"node": NODE, "kind": 1, "level": 2, "distance_cm": 40, "seq": 5})

    def test_alerts_come_out_oldest_first(self):
        rte.RTE_vInit()
        for seq, dist in ((1, 100), (2, 90), (3, 80)):
            self.inject(encode_alert(seq, 1, 1, dist, NODE))
        self.assertEqual([self.get()["distance_cm"] for _ in range(3)], [100, 90, 80])

    def test_tampered_packet_is_not_returned(self):
        rte.RTE_vInit()
        raw = bytearray(encode_alert(1, 1, 3, 20, NODE))
        raw[4] ^= 0x01
        self.inject(bytes(raw))
        self.assertIsNone(self.get())

    def test_wrong_key_packet_is_not_returned(self):
        rte.RTE_vInit()
        os.environ["MESH_HMAC_KEY"] = "attacker-key"
        raw = encode_alert(1, 1, 3, 20, NODE)
        os.environ["MESH_HMAC_KEY"] = _ENV["MESH_HMAC_KEY"]
        self.inject(raw)
        self.assertIsNone(self.get())

    def test_replayed_and_older_packets_are_not_returned(self):
        rte.RTE_vInit()
        self.inject(encode_alert(10, 1, 2, 40, NODE))
        self.assertEqual(self.get()["seq"], 10)
        self.inject(encode_alert(10, 1, 2, 40, NODE))             # replay
        self.assertIsNone(self.get())
        self.inject(encode_alert(9, 1, 2, 40, NODE))              # older
        self.assertIsNone(self.get())
        self.inject(encode_alert(11, 1, 2, 41, NODE))             # newer is fine again
        self.assertEqual(self.get()["seq"], 11)

    def test_sequence_wrap_around_is_accepted(self):
        rte.RTE_vInit()
        self.inject(encode_alert(255, 1, 1, 100, NODE))
        self.inject(encode_alert(0, 1, 1, 90, NODE))
        self.assertEqual([self.get()["seq"], self.get()["seq"]], [255, 0])


class MeshtasticTests(_RadioTests, _RteTestCase):
    status = staticmethod(rte.RTE_u8GetMeshStatus)
    send = staticmethod(rte.RTE_u16SendMeshAlert)
    get = staticmethod(rte.RTE_tGetMeshAlert)
    link_name = "_mesh"

    def test_send_text_returns_length_and_is_transmitted(self):
        rte.RTE_vInit()
        self.assertEqual(rte.RTE_u16SendMeshText("TEST-MESH hello"), 15)
        events = rte._mesh.poll()
        self.assertTrue(any(e.get("kind") == "tx" and e.get("payload") == "TEST-MESH hello" for e in events))

    def test_send_text_counts_utf8_bytes(self):
        rte.RTE_vInit()
        self.assertEqual(rte.RTE_u16SendMeshText("ab"), 2)

    def test_invalid_text_returns_zero(self):
        rte.RTE_vInit()
        for bad in ("", None, 5, b"abc", ["a"], "x" * (rte.RTE_MESH_TEXT_MAX_BYTES + 1)):
            self.assertEqual(rte.RTE_u16SendMeshText(bad), 0, repr(bad)[:20])

    def test_text_boundary_length_is_accepted(self):
        rte.RTE_vInit()
        self.assertEqual(rte.RTE_u16SendMeshText("x" * rte.RTE_MESH_TEXT_MAX_BYTES), rte.RTE_MESH_TEXT_MAX_BYTES)

    def test_send_text_before_init_returns_zero(self):
        self.assertEqual(rte.RTE_u16SendMeshText("hello"), 0)

    def test_a_text_message_is_not_an_alert(self):
        rte.RTE_vInit()
        rte._mesh.transport.inject_rx(b"hello", portnum=1)
        self.assertIsNone(rte.RTE_tGetMeshAlert())

    def test_status_is_zero_when_the_real_modem_is_missing(self):
        rte.RTE_vInit()
        rte._mesh = MeshtasticLink(port="/dev/does-not-exist-xyz", simulated=False, node_id=NODE)
        self.assertFalse(rte._mesh.connect())
        self.assertEqual(rte.RTE_u8GetMeshStatus(), 0)
        self.assertEqual(rte.RTE_u16SendMeshAlert(1, 1, 50), 0)
        self.assertEqual(rte.RTE_u16SendMeshText("hello"), 0)
        self.assertIsNone(rte.RTE_tGetMeshAlert())


class ZigbeeTests(_RadioTests, _RteTestCase):
    status = staticmethod(rte.RTE_u8GetZigbeeStatus)
    send = staticmethod(rte.RTE_u16SendZigbeeAlert)
    get = staticmethod(rte.RTE_tGetZigbeeAlert)
    link_name = "_zigbee"
    framed = True

    def test_zigbee_packet_is_framed(self):
        rte.RTE_vInit()
        sent = rte.RTE_u16SendZigbeeAlert(1, 1, 60)
        raw = rte._zigbee.transport.sent_packets[-1]
        self.assertEqual(raw[:2], SYNC)
        self.assertEqual(len(raw), sent)

    def test_frame_split_across_reads_and_garbage_are_handled(self):
        rte.RTE_vInit()
        wire = b"\x00\x7e\x55" + frame_encode(encode_alert(4, 1, 3, 25, NODE))
        rte._zigbee.transport.inject_rx(wire[:6])
        self.assertIsNone(rte.RTE_tGetZigbeeAlert())
        rte._zigbee.transport.inject_rx(wire[6:])
        self.assertEqual(rte.RTE_tGetZigbeeAlert()["distance_cm"], 25)

    def test_status_is_zero_when_the_real_xbee_is_missing(self):
        rte.RTE_vInit()
        rte._zigbee = ZigbeeLink(port="/dev/does-not-exist-xyz", simulated=False, node_id=NODE)
        self.assertFalse(rte._zigbee.connect())
        self.assertEqual(rte.RTE_u8GetZigbeeStatus(), 0)
        self.assertEqual(rte.RTE_u16SendZigbeeAlert(1, 1, 50), 0)

    def test_the_two_radios_are_independent(self):
        rte.RTE_vInit()
        self.assertGreater(rte.RTE_u16SendZigbeeAlert(1, 1, 50), 0)
        self.assertEqual(len(rte._mesh.transport.sent_packets), 0)
        self.assertGreater(rte.RTE_u16SendMeshAlert(1, 1, 50), 0)
        self.assertEqual(len(rte._zigbee.transport.sent_packets), 1)


# ----------------------------------------------------------------------------- signals
class SignalTests(_RteTestCase):
    # (write function, read function, valid values, invalid values, default)
    CASES = {
        "distance": (rte.RTE_vWriteDistanceCm, rte.RTE_u16ReadDistanceCm, (0, 1, 150, 400, 0xFFFF),
                     (-1, 0x10000, 1.5, True, None, "5"), 0xFFFF),
        "alert level": (rte.RTE_vWriteAlertLevel, rte.RTE_u8ReadAlertLevel, (0, 1, 2, 3),
                        (-1, 4, 255, 1.0, True, None, "2"), 0),
        "remote level": (rte.RTE_vWriteRemoteAlertLevel, rte.RTE_u8ReadRemoteAlertLevel, (0, 1, 2, 3),
                         (-1, 4, 255, 1.0, True, None, "2"), 0),
        "impact": (rte.RTE_vWriteImpact, rte.RTE_u8ReadImpact, (0, 1), BAD_BINARY, 0),
    }

    def test_defaults(self):
        for name, (_, read, _, _, default) in self.CASES.items():
            self.assertEqual(read(), default, name)

    def test_write_read_round_trip(self):
        for name, (write, read, valid, _, _) in self.CASES.items():
            for value in valid:
                write(value)
                self.assertEqual(read(), value, "%s=%r" % (name, value))

    def test_invalid_values_are_ignored(self):
        for name, (write, read, valid, invalid, _) in self.CASES.items():
            write(valid[-1])
            for bad in invalid:
                write(bad)
                self.assertEqual(read(), valid[-1], "%s bad=%r" % (name, bad))

    def test_signals_are_independent(self):
        rte.RTE_vWriteAlertLevel(3)
        rte.RTE_vWriteRemoteAlertLevel(1)
        rte.RTE_vWriteImpact(1)
        rte.RTE_vWriteDistanceCm(42)
        self.assertEqual((rte.RTE_u8ReadAlertLevel(), rte.RTE_u8ReadRemoteAlertLevel(), rte.RTE_u8ReadImpact(),
                          rte.RTE_u16ReadDistanceCm()), (3, 1, 1, 42))

    def test_signals_work_without_init(self):
        rte.RTE_vWriteAlertLevel(2)
        self.assertEqual(rte.RTE_u8ReadAlertLevel(), 2)

    def test_writers_return_none(self):
        self.assertIsNone(rte.RTE_vWriteAlertLevel(1))
        self.assertIsNone(rte.RTE_vWriteImpact(7))

    def test_thread_safety_with_8_threads(self):
        errors = []
        start = threading.Barrier(8)

        def worker(index):
            try:
                start.wait()
                for i in range(2000):
                    level = (index + i) % 4
                    rte.RTE_vWriteAlertLevel(level)
                    rte.RTE_vWriteRemoteAlertLevel(3 - level)
                    rte.RTE_vWriteImpact(i & 1)
                    rte.RTE_vWriteDistanceCm((index * 1000 + i) & 0xFFFF)
                    got = (rte.RTE_u8ReadAlertLevel(), rte.RTE_u8ReadRemoteAlertLevel(), rte.RTE_u8ReadImpact(),
                           rte.RTE_u16ReadDistanceCm())
                    if not (0 <= got[0] <= 3 and 0 <= got[1] <= 3 and got[2] in (0, 1) and 0 <= got[3] <= 0xFFFF):
                        errors.append(got)
            except Exception as exc:          # pragma: no cover - only on failure
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        self.assertFalse(any(t.is_alive() for t in threads))
        self.assertEqual(errors, [])

    def test_thread_safety_of_concurrent_sensor_and_radio_use(self):
        rte.RTE_vInit()
        ultrasonic.US_vSimSetDistanceCm(120)
        errors = []

        def reader():
            try:
                for _ in range(300):
                    if rte.RTE_u16GetDistanceCm() != 120:
                        errors.append("distance")
                    if rte.RTE_u16SendZigbeeAlert(1, 1, 120) == 0:
                        errors.append("send")
            except Exception as exc:          # pragma: no cover - only on failure
                errors.append(exc)

        threads = [threading.Thread(target=reader) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        self.assertEqual(errors, [])


# ------------------------------------------------------------------------ documentation
class DocumentationTests(unittest.TestCase):
    DOC = os.path.join(os.path.dirname(__file__), "..", "docs", "rte_api.md")

    @staticmethod
    def public_functions():
        return sorted(name for name, obj in inspect.getmembers(rte, inspect.isfunction)
                      if name.startswith("RTE_") and obj.__module__ == rte.__name__)

    def documented(self):
        with open(self.DOC, encoding="utf-8") as handle:
            return re.findall(r"^###\s+`?(RTE_\w+)`?\s*$", handle.read(), flags=re.M)

    def test_number_of_documented_functions_equals_public_functions(self):
        self.assertEqual(len(self.documented()), len(self.public_functions()))

    def test_every_public_function_is_documented_exactly_once(self):
        documented = self.documented()
        self.assertEqual(sorted(documented), self.public_functions())
        self.assertEqual(len(documented), len(set(documented)))

    def test_every_public_function_has_a_docstring(self):
        for name in self.public_functions():
            self.assertTrue(inspect.getdoc(getattr(rte, name)), name)

    def test_function_set_from_the_requirements(self):
        self.assertEqual(len(self.public_functions()), 26)

    def test_naming_convention(self):
        for name in self.public_functions():
            self.assertRegex(name, r"^RTE_(v|u8|u16|b|t)[A-Z]")


if __name__ == "__main__":
    unittest.main()
