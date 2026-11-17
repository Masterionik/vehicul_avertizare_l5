"""test.py: tests all MCD drivers (requirement 2 of lab L4).

Run (from the project root):
    MCD_SIMULATE=1 python -m mcd.test     # drivers only, no Django
    MCD_SIMULATE=1 python manage.py test  # through Django (also discovers mcd/test.py)

The normal tests force simulation (MCD_SIMULATE=1), so they run on any PC and
never switch the real buzzer on. On the Raspberry Pi, for the real hardware test:
    MCD_HW_TESTS=1 python -m mcd.test HardwareSmokeTest
"""
import builtins
import io
import math
import os
import unittest

import tempfile
import threading
from unittest import mock

from . import config, pcs, tim, ultrasonic, usb


# ------------------------------------------------------------------ test helpers
class FakeClock:
    """Controllable clock (seconds) for the TIM tests."""

    def __init__(self, t=0.0):
        self.t = t

    def __call__(self):
        return self.t


class _DriverTestCase(unittest.TestCase):
    MODE = "1"          # value of MCD_SIMULATE during the test

    @staticmethod
    def _reset_drivers():
        ultrasonic.US_vDeinit()
        pcs.PCS_vDeinit()
        usb.USB_vCloseAll()
        tim.TIM_vInit()

    def setUp(self):
        old = os.environ.get(config.SIM_ENV)
        os.environ[config.SIM_ENV] = self.MODE

        def restore():
            self._reset_drivers()
            if old is None:
                os.environ.pop(config.SIM_ENV, None)
            else:
                os.environ[config.SIM_ENV] = old

        self._reset_drivers()
        self.addCleanup(restore)


# ------------------------------------------------------------------------ PCS
class PcsTests(_DriverTestCase):
    def test_init_outputs_off_and_simulated(self):
        pcs.PCS_vInit()
        self.assertTrue(pcs.PCS_bIsSimulated())
        self.assertEqual(pcs.PCS_u8GetGpio(config.GPIO_BUZZER), pcs.PCS_PIN_OFF)
        self.assertEqual(pcs.PCS_u8GetGpio(config.GPIO_LED), pcs.PCS_PIN_OFF)

    def test_set_buzzer_and_led(self):
        pcs.PCS_vSetOutputGpio(config.GPIO_BUZZER, pcs.PCS_PIN_ON)
        self.assertEqual(pcs.PCS_u8GetGpio(config.GPIO_BUZZER), 1)
        self.assertEqual(pcs.PCS_u8GetGpio(config.GPIO_LED), 0)
        pcs.PCS_vSetOutputGpio(config.GPIO_LED, pcs.PCS_PIN_ON)
        self.assertEqual(pcs.PCS_u8GetGpio(config.GPIO_LED), 1)
        pcs.PCS_vSetOutputGpio(config.GPIO_BUZZER, pcs.PCS_PIN_OFF)
        self.assertEqual(pcs.PCS_u8GetGpio(config.GPIO_BUZZER), 0)
        self.assertEqual(pcs.PCS_u8GetGpio(config.GPIO_LED), 1)

    def test_invalid_pin_is_ignored(self):
        pcs.PCS_vInit()
        pcs.PCS_vSetOutputGpio(config.GPIO_BUZZER, 1)
        for bad in (28, 255, -1, "17", None, 3.5, True):
            pcs.PCS_vSetOutputGpio(bad, 1)
        self.assertEqual(pcs.PCS_u8GetGpio(config.GPIO_BUZZER), 1)
        self.assertEqual(pcs.PCS_u8GetGpio(config.GPIO_LED), 0)
        self.assertEqual(pcs.PCS_u8GetGpio(28), pcs.PCS_PIN_INVALID)

    def test_invalid_value_is_ignored(self):
        pcs.PCS_vSetOutputGpio(config.GPIO_BUZZER, 1)
        for bad in (2, -1, "1", None, 1.0):
            pcs.PCS_vSetOutputGpio(config.GPIO_BUZZER, bad)
        self.assertEqual(pcs.PCS_u8GetGpio(config.GPIO_BUZZER), 1)

    def test_pin_range_limits(self):
        pcs.PCS_vSetOutputGpio(config.PCS_PIN_MIN, 1)
        pcs.PCS_vSetOutputGpio(config.PCS_PIN_MAX, 1)
        self.assertEqual(pcs.PCS_u8GetGpio(config.PCS_PIN_MIN), 1)
        self.assertEqual(pcs.PCS_u8GetGpio(config.PCS_PIN_MAX), 1)

    def test_input_pin_read(self):
        pcs.PCS_vConfigInput(5)
        self.assertEqual(pcs.PCS_u8GetGpio(5), 0)
        pcs.PCS_vSimSetInput(5, 1)
        self.assertEqual(pcs.PCS_u8GetGpio(5), 1)

    def test_output_on_input_pin_reconfigures(self):
        pcs.PCS_vConfigInput(5)
        pcs.PCS_vSetOutputGpio(5, 1)
        self.assertEqual(pcs.PCS_u8GetGpio(5), 1)

    def test_deinit_turns_outputs_off(self):
        pcs.PCS_vSetOutputGpio(config.GPIO_BUZZER, 1)
        pcs.PCS_vSetOutputGpio(config.GPIO_LED, 1)
        pcs.PCS_vDeinit()
        pcs.PCS_vInit()
        self.assertEqual(pcs.PCS_u8GetGpio(config.GPIO_BUZZER), 0)
        self.assertEqual(pcs.PCS_u8GetGpio(config.GPIO_LED), 0)


# ------------------------------------------------------------------------ USB
class UsbTests(_DriverTestCase):
    def test_open_close(self):
        self.assertFalse(usb.USB_bIsOpen(config.USB_CH_ZIGBEE))
        usb.USB_vOpen(config.USB_CH_ZIGBEE)
        usb.USB_vOpen(config.USB_CH_ZIGBEE)                # idempotent
        self.assertTrue(usb.USB_bIsOpen(config.USB_CH_ZIGBEE))
        self.assertTrue(usb.USB_bIsSimulated(config.USB_CH_ZIGBEE))
        usb.USB_vClose(config.USB_CH_ZIGBEE)
        self.assertFalse(usb.USB_bIsOpen(config.USB_CH_ZIGBEE))

    def test_write_is_captured(self):
        usb.USB_vOpen(config.USB_CH_MESHTASTIC)
        self.assertEqual(usb.USB_u16Write(config.USB_CH_MESHTASTIC, b"\x01\x02\x03"), 3)
        self.assertEqual(usb.USB_u16Write(config.USB_CH_MESHTASTIC, bytearray(b"AB")), 2)
        self.assertEqual(usb.USB_baSimTakeTx(config.USB_CH_MESHTASTIC), b"\x01\x02\x03AB")
        self.assertEqual(usb.USB_baSimTakeTx(config.USB_CH_MESHTASTIC), b"")

    def test_read_partial_and_empty(self):
        ch = config.USB_CH_ZIGBEE
        usb.USB_vOpen(ch)
        self.assertEqual(usb.USB_baRead(ch), b"")
        usb.USB_vSimInject(ch, b"0123456789")
        self.assertEqual(usb.USB_u16Available(ch), 10)
        self.assertEqual(usb.USB_baRead(ch, 4), b"0123")
        self.assertEqual(usb.USB_baRead(ch), b"456789")
        self.assertEqual(usb.USB_u16Available(ch), 0)

    def test_flush(self):
        ch = config.USB_CH_ZIGBEE
        usb.USB_vOpen(ch)
        usb.USB_vSimInject(ch, b"xyz")
        usb.USB_vFlush(ch)
        self.assertEqual(usb.USB_u16Available(ch), 0)

    def test_channels_are_independent(self):
        usb.USB_vOpen(config.USB_CH_ZIGBEE)
        usb.USB_vOpen(config.USB_CH_MESHTASTIC)
        usb.USB_vSimInject(config.USB_CH_ZIGBEE, b"Z")
        usb.USB_u16Write(config.USB_CH_MESHTASTIC, b"M")
        self.assertEqual(usb.USB_u16Available(config.USB_CH_MESHTASTIC), 0)
        self.assertEqual(usb.USB_baSimTakeTx(config.USB_CH_ZIGBEE), b"")
        self.assertEqual(usb.USB_baSimTakeTx(config.USB_CH_MESHTASTIC), b"M")

    def test_closed_channel_raises(self):
        with self.assertRaises(usb.USBError):
            usb.USB_u16Write(config.USB_CH_ZIGBEE, b"x")
        with self.assertRaises(usb.USBError):
            usb.USB_baRead(config.USB_CH_ZIGBEE)

    def test_invalid_arguments(self):
        with self.assertRaises(ValueError):
            usb.USB_vOpen(99)
        usb.USB_vOpen(config.USB_CH_ZIGBEE)
        with self.assertRaises(ValueError):
            usb.USB_u16Write(config.USB_CH_ZIGBEE, "text")
        with self.assertRaises(ValueError):
            usb.USB_baRead(config.USB_CH_ZIGBEE, 0)

    def test_port_override_from_env(self):
        old = os.environ.get("MCD_ZIGBEE_PORT")
        os.environ["MCD_ZIGBEE_PORT"] = "/dev/ttyUSB7"
        try:
            self.assertEqual(config.usb_config(config.USB_CH_ZIGBEE)["port"], "/dev/ttyUSB7")
        finally:
            if old is None:
                os.environ.pop("MCD_ZIGBEE_PORT", None)
            else:
                os.environ["MCD_ZIGBEE_PORT"] = old


# ------------------------------------------------------------------------ TIM
class TimTests(_DriverTestCase):
    def setUp(self):
        super().setUp()
        self.clk = FakeClock(0.0)
        tim.TIM_vSetClock(self.clk)

    def test_periodic_fires_once_per_period(self):
        tim.TIM_vStart(tim.TIM_CH_1S, 1000)
        self.clk.t = 0.999
        self.assertFalse(tim.TIM_bHasElapsed(tim.TIM_CH_1S))
        self.clk.t = 1.0
        self.assertTrue(tim.TIM_bHasElapsed(tim.TIM_CH_1S))
        self.assertFalse(tim.TIM_bHasElapsed(tim.TIM_CH_1S))
        self.clk.t = 1.999
        self.assertFalse(tim.TIM_bHasElapsed(tim.TIM_CH_1S))
        self.clk.t = 2.0
        self.assertTrue(tim.TIM_bHasElapsed(tim.TIM_CH_1S))

    def test_missed_periods_resync_without_burst(self):
        tim.TIM_vStart(tim.TIM_CH_1S, 1000)
        self.clk.t = 5.5
        self.assertTrue(tim.TIM_bHasElapsed(tim.TIM_CH_1S))
        self.assertFalse(tim.TIM_bHasElapsed(tim.TIM_CH_1S))
        self.clk.t = 5.9
        self.assertFalse(tim.TIM_bHasElapsed(tim.TIM_CH_1S))
        self.clk.t = 6.0
        self.assertTrue(tim.TIM_bHasElapsed(tim.TIM_CH_1S))

    def test_channels_are_independent(self):
        tim.TIM_vStart(tim.TIM_CH_1S, 1000)
        tim.TIM_vStart(tim.TIM_CH_60S, 60000)
        self.clk.t = 1.0
        self.assertTrue(tim.TIM_bHasElapsed(tim.TIM_CH_1S))
        self.assertFalse(tim.TIM_bHasElapsed(tim.TIM_CH_60S))
        self.clk.t = 60.0
        self.assertTrue(tim.TIM_bHasElapsed(tim.TIM_CH_60S))

    def test_oneshot(self):
        tim.TIM_vStart(tim.TIM_CH_ALERT, 5000, tim.TIM_MODE_ONESHOT)
        self.assertTrue(tim.TIM_bIsRunning(tim.TIM_CH_ALERT))
        self.clk.t = 4.9
        self.assertFalse(tim.TIM_bHasElapsed(tim.TIM_CH_ALERT))
        self.clk.t = 5.0
        self.assertTrue(tim.TIM_bHasElapsed(tim.TIM_CH_ALERT))
        self.assertFalse(tim.TIM_bIsRunning(tim.TIM_CH_ALERT))
        self.clk.t = 50.0
        self.assertFalse(tim.TIM_bHasElapsed(tim.TIM_CH_ALERT))

    def test_stop(self):
        tim.TIM_vStart(tim.TIM_CH_1S, 1000)
        tim.TIM_vStop(tim.TIM_CH_1S)
        self.clk.t = 10.0
        self.assertFalse(tim.TIM_bIsRunning(tim.TIM_CH_1S))
        self.assertFalse(tim.TIM_bHasElapsed(tim.TIM_CH_1S))

    def test_invalid_arguments(self):
        for call in (
            lambda: tim.TIM_vStart(tim.TIM_NUM_CHANNELS, 1000),
            lambda: tim.TIM_vStart(-1, 1000),
            lambda: tim.TIM_vStart(tim.TIM_CH_1S, 0),
            lambda: tim.TIM_vStart(tim.TIM_CH_1S, 1000, mode=7),
            lambda: tim.TIM_bHasElapsed("a"),
            lambda: tim.TIM_vDelayMs(-5),
        ):
            with self.assertRaises(ValueError):
                call()

    def test_get_ms_and_32bit_wrap(self):
        self.clk.t = 12.5
        self.assertEqual(tim.TIM_u32GetMs(), 12500)
        self.clk.t = 4294967.5                              # 4294967500 ms > 2^32
        self.assertEqual(tim.TIM_u32GetMs(), 4294967500 - 2 ** 32)

    def test_real_clock_is_monotonic(self):
        tim.TIM_vInit()
        a = tim.TIM_u32GetMs()
        tim.TIM_vDelayMs(5)
        self.assertGreaterEqual(tim.TIM_u32GetMs() - a, 4)


# ----------------------------------------------------------------- ultrasonic
class FakeSensor:
    """Fake HC-SR04 behind the pin functions, with a fake clock (every clock read costs 1 us).

    echo_us=None: the sensor never answers; echo_us="stuck": ECHO stays high.
    """

    def __init__(self, echo_us=None, delay_us=400, step_ns=1000):
        self.t = 0
        self.step = step_ns
        self.echo_us = echo_us
        self.delay_us = delay_us
        self.trig = 0
        self.trig_rise = None
        self.trig_fall = None
        self.trig_width_ns = None
        self.measurements = 0
        self.overlap = False
        self._busy = False

    def now(self):
        self.t += self.step
        return self.t

    def write(self, pin, value):
        if pin != config.GPIO_US_TRIG:
            return
        if value == 1 and self.trig == 0:
            if self._busy:
                self.overlap = True              # a second measurement started before the first ended
            self._busy = True
            self.measurements += 1
            self.trig_rise = self.t
            self.trig_fall = None
        elif value == 0 and self.trig == 1:
            self.trig_fall = self.t
            self.trig_width_ns = self.t - self.trig_rise
        self.trig = value

    def read(self, pin):
        if pin != config.GPIO_US_ECHO or self.trig_fall is None or self.echo_us is None:
            return 0
        start = self.trig_fall + self.delay_us * 1000
        if self.echo_us == "stuck":
            return 1 if self.t >= start else 0
        end = start + int(self.echo_us * 1000)
        if self.t >= end:
            self._busy = False
        return 1 if start <= self.t < end else 0


class UltrasonicTests(_DriverTestCase):
    def test_not_valid_before_first_read(self):
        self.assertFalse(ultrasonic.US_bLastReadValid())

    def test_simulation_default_and_settable_distance(self):
        self.assertTrue(ultrasonic.US_bIsSimulated())
        self.assertEqual(ultrasonic.US_u16ReadDistanceCm(), 300)
        self.assertTrue(ultrasonic.US_bLastReadValid())
        ultrasonic.US_vSimSetDistanceCm(100)
        self.assertEqual(ultrasonic.US_u16ReadDistanceCm(), 100)

    def test_valid_range_boundaries(self):
        for cm in (2, 3, 399, 400):
            ultrasonic.US_vSimSetDistanceCm(cm)
            self.assertEqual(ultrasonic.US_u16ReadDistanceCm(), cm)
            self.assertTrue(ultrasonic.US_bLastReadValid())

    def test_out_of_range_is_invalid(self):
        for cm in (0, 1, 401, 500, 5000):
            ultrasonic.US_vSimSetDistanceCm(cm)
            self.assertEqual(ultrasonic.US_u16ReadDistanceCm(), ultrasonic.US_DISTANCE_INVALID, cm)
            self.assertFalse(ultrasonic.US_bLastReadValid())

    def test_no_echo_is_invalid_and_recovers(self):
        ultrasonic.US_vSimSetDistanceCm(None)
        self.assertEqual(ultrasonic.US_u16ReadDistanceCm(), 0xFFFF)
        self.assertFalse(ultrasonic.US_bLastReadValid())
        ultrasonic.US_vSimSetDistanceCm(120)
        self.assertEqual(ultrasonic.US_u16ReadDistanceCm(), 120)
        self.assertTrue(ultrasonic.US_bLastReadValid())

    def test_invalid_simulated_value_is_ignored(self):
        ultrasonic.US_vSimSetDistanceCm(100)
        for bad in ("5", True, -1, 70000, 0xFFFF, 1.5, [1]):
            ultrasonic.US_vSimSetDistanceCm(bad)
        self.assertEqual(ultrasonic.US_u16ReadDistanceCm(), 100)

    def test_deinit_resets_simulation(self):
        ultrasonic.US_vSimSetDistanceCm(50)
        ultrasonic.US_u16ReadDistanceCm()
        ultrasonic.US_vDeinit()
        self.assertFalse(ultrasonic.US_bLastReadValid())
        self.assertEqual(ultrasonic.US_u16ReadDistanceCm(), 300)

    def _hw(self, sensor):
        ultrasonic._use_backend(sensor.write, sensor.read, sensor.now)

    def test_measurement_on_pins_converts_echo_time(self):
        for cm in (2, 30, 80, 150, 399, 400):
            sensor = FakeSensor(echo_us=cm * 58)
            self._hw(sensor)
            self.assertFalse(ultrasonic.US_bIsSimulated())
            self.assertEqual(ultrasonic.US_u16ReadDistanceCm(), cm)
            self.assertTrue(ultrasonic.US_bLastReadValid())

    def test_trigger_pulse_is_at_least_10_us_and_ends_low(self):
        sensor = FakeSensor(echo_us=100 * 58)
        self._hw(sensor)
        ultrasonic.US_u16ReadDistanceCm()
        self.assertGreaterEqual(sensor.trig_width_ns, 10_000)
        self.assertLess(sensor.trig_width_ns, 100_000)
        self.assertEqual(sensor.trig, 0)

    def test_echo_too_short_or_too_long_is_invalid(self):
        for echo_us in (58, 20, 401 * 58, 450 * 58):        # 1 cm, 0.3 cm, 401 cm, 450 cm
            self._hw(FakeSensor(echo_us=echo_us))
            self.assertEqual(ultrasonic.US_u16ReadDistanceCm(), 0xFFFF, echo_us)
            self.assertFalse(ultrasonic.US_bLastReadValid())

    def test_timeout_without_echo(self):
        sensor = FakeSensor(echo_us=None)
        self._hw(sensor)
        self.assertEqual(ultrasonic.US_u16ReadDistanceCm(), 0xFFFF)
        self.assertFalse(ultrasonic.US_bLastReadValid())
        self.assertGreaterEqual(sensor.t, 30_000_000)        # waited the full 30 ms
        self.assertLess(sensor.t, 31_000_000)                # and not much longer

    def test_timeout_when_echo_stays_high(self):
        sensor = FakeSensor(echo_us="stuck")
        self._hw(sensor)
        self.assertEqual(ultrasonic.US_u16ReadDistanceCm(), 0xFFFF)
        self.assertFalse(ultrasonic.US_bLastReadValid())

    def test_threads_never_overlap_measurements_on_pins(self):
        sensor = FakeSensor(echo_us=100 * 58)
        self._hw(sensor)
        results = []

        def worker():
            for _ in range(5):
                results.append(ultrasonic.US_u16ReadDistanceCm())

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(results, [100] * 40)
        self.assertEqual(sensor.measurements, 40)
        self.assertFalse(sensor.overlap)

    def test_threads_in_simulation_get_consistent_values(self):
        allowed = {100, 200}
        seen = []
        stop = threading.Event()

        def toggler():
            while not stop.is_set():
                for cm in (100, 200):
                    ultrasonic.US_vSimSetDistanceCm(cm)

        def worker():
            for _ in range(200):
                seen.append(ultrasonic.US_u16ReadDistanceCm())

        ultrasonic.US_vSimSetDistanceCm(100)
        tog = threading.Thread(target=toggler)
        tog.start()
        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        stop.set()
        tog.join()
        self.assertEqual(len(seen), 1600)
        self.assertTrue(set(seen) <= allowed)


# --------------------------------------------------------------- edge counter
class EdgeCounterTests(_DriverTestCase):
    PIN = config.GPIO_VIB[0]

    def test_inject_count_and_clear(self):
        pcs.PCS_vConfigEdgeCounter(self.PIN)
        self.assertEqual(pcs.PCS_sEdgeCounterMode(self.PIN), "sim")
        self.assertEqual(pcs.PCS_u16GetEdgeCount(self.PIN, False), 0)
        pcs.PCS_vSimInjectEdges(self.PIN, 5)
        pcs.PCS_vSimInjectEdges(self.PIN, 2)
        self.assertEqual(pcs.PCS_u16GetEdgeCount(self.PIN, False), 7)
        self.assertEqual(pcs.PCS_u16GetEdgeCount(self.PIN, False), 7)     # reading does not consume
        self.assertEqual(pcs.PCS_u16GetEdgeCount(self.PIN, True), 7)
        self.assertEqual(pcs.PCS_u16GetEdgeCount(self.PIN, False), 0)

    def test_counters_are_per_pin(self):
        pcs.PCS_vConfigEdgeCounter(22)
        pcs.PCS_vConfigEdgeCounter(25)
        pcs.PCS_vSimInjectEdges(22, 3)
        pcs.PCS_vSimInjectEdges(25, 1)
        self.assertEqual(pcs.PCS_u16GetEdgeCount(22, True), 3)
        self.assertEqual(pcs.PCS_u16GetEdgeCount(25, True), 1)

    def test_pin_without_counter_and_invalid_arguments(self):
        pcs.PCS_vSimInjectEdges(self.PIN, 4)                  # not configured: ignored
        self.assertEqual(pcs.PCS_u16GetEdgeCount(self.PIN, False), 0)
        for bad in (28, -1, "22", None, 2.5, True):
            pcs.PCS_vConfigEdgeCounter(bad)
            self.assertEqual(pcs.PCS_u16GetEdgeCount(bad, False), 0)
        pcs.PCS_vConfigEdgeCounter(self.PIN)
        for bad in (-1, "3", None, 1.5, True):
            pcs.PCS_vSimInjectEdges(self.PIN, bad)
        self.assertEqual(pcs.PCS_u16GetEdgeCount(self.PIN, False), 0)

    def test_configure_twice_keeps_the_count(self):
        pcs.PCS_vConfigEdgeCounter(self.PIN)
        pcs.PCS_vSimInjectEdges(self.PIN, 2)
        pcs.PCS_vConfigEdgeCounter(self.PIN)
        self.assertEqual(pcs.PCS_u16GetEdgeCount(self.PIN, False), 2)

    def test_count_saturates_at_u16(self):
        pcs.PCS_vConfigEdgeCounter(self.PIN)
        pcs.PCS_vSimInjectEdges(self.PIN, 0xFFFF)
        pcs.PCS_vSimInjectEdges(self.PIN, 10)
        self.assertEqual(pcs.PCS_u16GetEdgeCount(self.PIN, False), 0xFFFF)

    def test_debounce_ignores_edges_closer_than_5_ms(self):
        pcs.PCS_vConfigEdgeCounter(self.PIN)
        self.assertTrue(pcs._edge_event(self.PIN, 1_000_000_000))
        self.assertFalse(pcs._edge_event(self.PIN, 1_000_000_000 + 4_999_999))
        self.assertTrue(pcs._edge_event(self.PIN, 1_000_000_000 + 5_000_000))
        self.assertFalse(pcs._edge_event(self.PIN, 1_000_000_000 + 9_000_000))   # 4 ms after the last counted
        self.assertTrue(pcs._edge_event(self.PIN, 1_000_000_000 + 10_000_000))
        self.assertEqual(pcs.PCS_u16GetEdgeCount(self.PIN, False), 3)

    def test_simulated_input_rising_edges_are_counted_with_debounce(self):
        pcs.PCS_vConfigEdgeCounter(self.PIN)
        pcs.PCS_vSimSetInput(self.PIN, 1)           # rising: counted
        pcs.PCS_vSimSetInput(self.PIN, 1)           # no change: not an edge
        pcs.PCS_vSimSetInput(self.PIN, 0)           # falling: not counted
        pcs.PCS_vSimSetInput(self.PIN, 1)           # rising, microseconds later: debounced
        self.assertEqual(pcs.PCS_u16GetEdgeCount(self.PIN, False), 1)
        pcs.PCS_vSimSetInput(self.PIN, 0)
        tim.TIM_vDelayMs(8)
        pcs.PCS_vSimSetInput(self.PIN, 1)
        self.assertEqual(pcs.PCS_u16GetEdgeCount(self.PIN, False), 2)
        self.assertEqual(pcs.PCS_u8GetGpio(self.PIN), 1)

    def test_deinit_removes_counters(self):
        pcs.PCS_vConfigEdgeCounter(self.PIN)
        pcs.PCS_vSimInjectEdges(self.PIN, 3)
        pcs.PCS_vDeinit()
        self.assertEqual(pcs.PCS_u16GetEdgeCount(self.PIN, False), 0)
        self.assertIsNone(pcs.PCS_sEdgeCounterMode(self.PIN))

    def test_inject_is_refused_on_real_hardware(self):
        with mock.patch.object(pcs, "_simulated", False), mock.patch.object(pcs, "_initialized", True):
            with self.assertRaises(RuntimeError):
                pcs.PCS_vSimInjectEdges(self.PIN, 1)


class _FakeLgpio:
    """Minimal lgpio replacement to test the hardware edge sources (callback and polling)."""
    RISING_EDGE = 1
    SET_PULL_DOWN = 0x20

    def __init__(self, callback_works=True):
        self.callback_works = callback_works
        self.level = 0
        self.callbacks = []
        self.claimed = []
        self.cancelled = 0
        self.freed = []

    def gpio_claim_alert(self, handle, pin, edge, flags=0):
        self.claimed.append((pin, edge, flags))

    def gpio_free(self, handle, pin):
        self.freed.append(pin)

    def gpio_read(self, handle, pin):
        return self.level

    def callback(self, handle, pin, edge, func):
        if not self.callback_works:
            raise RuntimeError("no callbacks")
        fake = self
        fake.callbacks.append(func)

        class _Cb:
            def cancel(self_inner):
                fake.cancelled += 1
        return _Cb()


class EdgeSourceHardwarePathTests(_DriverTestCase):
    """The lgpio-specific code, exercised with a fake lgpio (no hardware needed)."""
    PIN = config.GPIO_VIB[0]

    def _start(self, fake):
        patches = [mock.patch.object(pcs, "lgpio", fake), mock.patch.object(pcs, "_handle", 7)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        entry = {"count": 0, "last_ns": None, "mode": None, "cb": None, "thread": None, "stop": None}
        pcs._edges[self.PIN] = entry
        self.addCleanup(pcs._stop_edge_counters)
        self.addCleanup(pcs._direction.pop, self.PIN, None)
        pcs._start_hw_edge_source(self.PIN, entry)
        return entry

    def test_callback_counts_rising_edges_with_driver_debounce(self):
        fake = _FakeLgpio()
        entry = self._start(fake)
        self.assertEqual(entry["mode"], "callback")
        self.assertEqual(fake.claimed, [(self.PIN, 1, 0x20)])           # pull-down for an unconnected sensor
        cb = fake.callbacks[0]
        cb(0, self.PIN, 1, 2_000_000_000)
        cb(0, self.PIN, 1, 2_002_000_000)                                # 2 ms later: bounce
        cb(0, self.PIN, 1, 2_010_000_000)
        self.assertEqual(pcs.PCS_u16GetEdgeCount(self.PIN, False), 2)

    def test_polling_fallback_when_callbacks_are_unavailable(self):
        fake = _FakeLgpio(callback_works=False)
        entry = self._start(fake)
        self.assertEqual(entry["mode"], "poll")
        fake.level = 1
        import time as _t
        end = _t.monotonic() + 2.0
        while pcs.PCS_u16GetEdgeCount(self.PIN, False) < 1 and _t.monotonic() < end:
            _t.sleep(0.002)
        self.assertEqual(pcs.PCS_u16GetEdgeCount(self.PIN, False), 1)    # high level: one edge, not many
        pcs._stop_edge_counters()
        self.assertFalse(entry["thread"].is_alive())

    def test_stop_cancels_the_callback(self):
        fake = _FakeLgpio()
        self._start(fake)
        pcs._stop_edge_counters()
        self.assertEqual(fake.cancelled, 1)


# ------------------------------------------------------------ port detection
class PortDetectionTests(_DriverTestCase):
    XBEE = "usb-FTDI_FT231X_USB_UART_DN05XYZ1-if00-port0"
    HELTEC_V3 = "usb-Silicon_Labs_CP2102_USB_to_UART_Bridge_Controller_0001-if00-port0"
    EINK = "usb-Espressif_USB_JTAG_serial_debug_unit_AA:BB:CC:DD:EE:FF-if00"
    XIAO = "usb-Seeed_Studio_XIAO_nRF52840_1234ABCD-if00"
    ENV_KEYS = ("ZIGBEE_PORT", "MCD_ZIGBEE_PORT", "MESHTASTIC_PORT", "MCD_MESHTASTIC_PORT")

    def _by_id(self, *names):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        for i, name in enumerate(names):
            # both boards may be /dev/ttyUSB0 depending on the plug order: only the by-id name counts
            os.symlink("/dev/ttyUSB0" if i % 2 == 0 else "/dev/ttyACM0", os.path.join(tmp.name, name))
        patcher = mock.patch.dict(os.environ, {config.SERIAL_BY_ID_ENV: tmp.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        for key in self.ENV_KEYS:
            os.environ.pop(key, None)
        return tmp.name

    def test_classification(self):
        self.assertEqual(config.classify_serial_id(self.XBEE), "xbee")
        self.assertEqual(config.classify_serial_id(self.HELTEC_V3), "meshtastic")
        self.assertEqual(config.classify_serial_id(self.EINK), "meshtastic")
        self.assertEqual(config.classify_serial_id(self.XIAO), "meshtastic")
        self.assertEqual(config.classify_serial_id("usb-Acme_Mouse-if00"), "unknown")

    def test_ports_are_identified_by_id_not_by_tty_number(self):
        directory = self._by_id(self.HELTEC_V3, self.XBEE)        # the Heltec is "ttyUSB0" here ...
        z = config.usb_config(config.USB_CH_ZIGBEE)
        m = config.usb_config(config.USB_CH_MESHTASTIC)
        self.assertEqual(z["port"], os.path.join(directory, self.XBEE))
        self.assertEqual(m["port"], os.path.join(directory, self.HELTEC_V3))
        self.assertEqual((z["source"], m["source"]), ("by-id", "by-id"))
        self.assertIsNone(z["error"])
        self.assertEqual(z["baud"], 9600)

    def test_modem_is_preferred_among_several_meshtastic_boards(self):
        directory = self._by_id(self.EINK, self.HELTEC_V3, self.XIAO, self.XBEE)
        m = config.usb_config(config.USB_CH_MESHTASTIC)
        self.assertEqual(m["port"], os.path.join(directory, self.HELTEC_V3))
        self.assertIsNone(m["error"])

    def test_ambiguous_meshtastic_requires_env_var(self):
        self._by_id(self.EINK, self.XIAO)
        m = config.usb_config(config.USB_CH_MESHTASTIC)
        self.assertIn("MESHTASTIC_PORT", m["error"])
        self.assertEqual(m["source"], "default")
        os.environ["MESHTASTIC_PORT"] = "/dev/ttyACM5"
        m = config.usb_config(config.USB_CH_MESHTASTIC)
        self.assertEqual((m["port"], m["source"], m["error"]), ("/dev/ttyACM5", "env", None))

    def test_ambiguous_zigbee_requires_env_var(self):
        self._by_id(self.XBEE, "usb-FTDI_FT232R_USB_UART_A50285BI-if00-port0")
        z = config.usb_config(config.USB_CH_ZIGBEE)
        self.assertIn("ZIGBEE_PORT", z["error"])
        self.assertIn("2 xbee devices", z["error"])

    def test_open_refuses_ambiguous_ports_on_real_runs_but_simulates_when_forced(self):
        self._by_id(self.XBEE, "usb-FTDI_FT232R_USB_UART_A50285BI-if00-port0")
        os.environ[config.SIM_ENV] = "auto"
        with self.assertRaises(usb.USBError) as ctx:
            usb.USB_vOpen(config.USB_CH_ZIGBEE)
        self.assertIn("ZIGBEE_PORT", str(ctx.exception))
        os.environ[config.SIM_ENV] = "1"
        usb.USB_vOpen(config.USB_CH_ZIGBEE)
        self.assertTrue(usb.USB_bIsSimulated(config.USB_CH_ZIGBEE))

    def test_env_overrides_and_legacy_names(self):
        self._by_id(self.XBEE)
        os.environ["MCD_ZIGBEE_PORT"] = "/dev/ttyUSB3"
        self.assertEqual(config.usb_config(config.USB_CH_ZIGBEE)["port"], "/dev/ttyUSB3")
        os.environ["ZIGBEE_PORT"] = "/dev/ttyUSB4"                 # the new name wins
        z = config.usb_config(config.USB_CH_ZIGBEE)
        self.assertEqual((z["port"], z["source"]), ("/dev/ttyUSB4", "env"))
        os.environ["MCD_MESHTASTIC_PORT"] = "/dev/ttyACM2"
        self.assertEqual(config.usb_config(config.USB_CH_MESHTASTIC)["port"], "/dev/ttyACM2")

    def test_no_devices_falls_back_to_default_names(self):
        self._by_id()
        z = config.usb_config(config.USB_CH_ZIGBEE)
        self.assertEqual((z["port"], z["source"], z["error"]), ("/dev/ttyUSB0", "default", None))

    def test_missing_by_id_directory_is_not_an_error(self):
        self._by_id()
        os.environ[config.SERIAL_BY_ID_ENV] = "/nonexistent/by-id"
        self.assertEqual(config.list_serial_devices(), [])

    def test_vibration_pins_and_optional_second_sensor(self):
        with mock.patch.dict(os.environ, {config.VIB2_ENV: "1"}):
            self.assertEqual(config.vibration_pins(), (22, 25))
        with mock.patch.dict(os.environ, {config.VIB2_ENV: "0"}):
            self.assertEqual(config.vibration_pins(), (22,))

    def test_pin_table_documents_all_signals(self):
        names = {row["signal_name"] for row in config.PIN_TABLE}
        for expected in ("BUZZER", "LED", "US_TRIG", "US_ECHO", "VIB1", "VIB2", "5V", "3V3", "GND",
                         "ZIGBEE", "MESHTASTIC"):
            self.assertIn(expected, names)
        echo = next(r for r in config.PIN_TABLE if r["signal_name"] == "US_ECHO")
        self.assertIn("divider", echo["definition"])
        self.assertEqual((config.GPIO_US_TRIG, config.GPIO_US_ECHO, config.GPIO_VIB), (23, 24, (22, 25)))


# ----------------------------------------------- full chain: alert -> outputs
class AlertChainTests(_DriverTestCase):
    """Buzzer + LED switched on by an alert and switched off when the alert timer expires (TIM)."""

    def test_alert_outputs_turn_on_and_expire(self):
        clk = FakeClock(0.0)
        tim.TIM_vSetClock(clk)
        self.assertEqual(pcs.PCS_u8GetGpio(config.GPIO_BUZZER), 0)   # at rest: no alert
        self.assertEqual(pcs.PCS_u8GetGpio(config.GPIO_LED), 0)

        pcs.PCS_vSetOutputGpio(config.GPIO_BUZZER, pcs.PCS_PIN_ON)    # alert raised
        pcs.PCS_vSetOutputGpio(config.GPIO_LED, pcs.PCS_PIN_ON)
        tim.TIM_vStart(tim.TIM_CH_ALERT, 5000, tim.TIM_MODE_ONESHOT)
        self.assertEqual(pcs.PCS_u8GetGpio(config.GPIO_BUZZER), 1)
        self.assertEqual(pcs.PCS_u8GetGpio(config.GPIO_LED), 1)

        clk.t = 4.9
        self.assertFalse(tim.TIM_bHasElapsed(tim.TIM_CH_ALERT))
        self.assertEqual(pcs.PCS_u8GetGpio(config.GPIO_BUZZER), 1)

        clk.t = 5.0
        self.assertTrue(tim.TIM_bHasElapsed(tim.TIM_CH_ALERT))
        pcs.PCS_vSetOutputGpio(config.GPIO_BUZZER, pcs.PCS_PIN_OFF)
        pcs.PCS_vSetOutputGpio(config.GPIO_LED, pcs.PCS_PIN_OFF)
        self.assertEqual(pcs.PCS_u8GetGpio(config.GPIO_BUZZER), 0)
        self.assertEqual(pcs.PCS_u8GetGpio(config.GPIO_LED), 0)


class PcsChipSelectionTests(unittest.TestCase):
    def test_detect_gpiochip_from_env_override(self):
        old = os.environ.get("MCD_GPIOCHIP")
        os.environ["MCD_GPIOCHIP"] = "gpiochip3"
        try:
            self.assertEqual(pcs._resolve_gpiochip(), 3)
        finally:
            if old is None:
                os.environ.pop("MCD_GPIOCHIP", None)
            else:
                os.environ["MCD_GPIOCHIP"] = old

    def test_detect_gpiochip_pi4_layout(self):
        old = os.environ.get("MCD_GPIOCHIP")
        os.environ.pop("MCD_GPIOCHIP", None)
        original_glob = pcs.glob.glob
        original_exists = os.path.exists
        original_open = builtins.open
        original_lgpio = pcs.lgpio
        try:
            pcs.glob.glob = lambda pattern: ["/dev/gpiochip0", "/dev/gpiochip1"]
            os.path.exists = lambda path: path == "/sys/class/gpio/gpiochip1/label"

            def fake_open(path, mode="r", *args, **kwargs):
                if path == "/sys/class/gpio/gpiochip1/label":
                    return io.StringIO("pinctrl-bcm2711")
                raise FileNotFoundError(path)

            pcs.lgpio = type("FakeLgpio", (), {"gpio_get_chip_info": lambda self, chip: {"label": "pinctrl-bcm2711" if chip == 1 else "pinctrl-bcm2835"}, "gpiochip_open": lambda self, chip: chip, "gpiochip_close": lambda self, chip: None})()
            builtins.open = fake_open
            self.assertEqual(pcs._resolve_gpiochip(), 1)
        finally:
            pcs.glob.glob = original_glob
            os.path.exists = original_exists
            builtins.open = original_open
            pcs.lgpio = original_lgpio
            if old is None:
                os.environ.pop("MCD_GPIOCHIP", None)
            else:
                os.environ["MCD_GPIOCHIP"] = old

    def test_detect_gpiochip_pi5_layout_old_numbering(self):
        old = os.environ.get("MCD_GPIOCHIP")
        os.environ.pop("MCD_GPIOCHIP", None)
        original_glob = pcs.glob.glob
        original_exists = os.path.exists
        original_open = builtins.open
        original_lgpio = pcs.lgpio
        try:
            pcs.glob.glob = lambda pattern: ["/dev/gpiochip4", "/dev/gpiochip0"]
            os.path.exists = lambda path: path == "/sys/class/gpio/gpiochip4/label"

            def fake_open(path, mode="r", *args, **kwargs):
                if path == "/sys/class/gpio/gpiochip4/label":
                    return io.StringIO("pinctrl-rp1")
                raise FileNotFoundError(path)

            pcs.lgpio = type("FakeLgpio", (), {"gpio_get_chip_info": lambda self, chip: {"label": "pinctrl-rp1" if chip == 4 else "pinctrl-bcm2835"}, "gpiochip_open": lambda self, chip: chip, "gpiochip_close": lambda self, chip: None})()
            builtins.open = fake_open
            self.assertEqual(pcs._resolve_gpiochip(), 4)
        finally:
            pcs.glob.glob = original_glob
            os.path.exists = original_exists
            builtins.open = original_open
            pcs.lgpio = original_lgpio
            if old is None:
                os.environ.pop("MCD_GPIOCHIP", None)
            else:
                os.environ["MCD_GPIOCHIP"] = old

    def test_detect_gpiochip_by_label_when_available(self):
        old = os.environ.get("MCD_GPIOCHIP")
        os.environ.pop("MCD_GPIOCHIP", None)
        try:
            original_glob = pcs.glob.glob
            original_exists = os.path.exists
            original_open = builtins.open
            original_lgpio = pcs.lgpio
            try:
                pcs.glob.glob = lambda pattern: ["/dev/gpiochip4", "/dev/gpiochip0"]
                os.path.exists = lambda path: path == "/sys/class/gpio/gpiochip4/label"

                def fake_open(path, mode="r", *args, **kwargs):
                    if path == "/sys/class/gpio/gpiochip4/label":
                        return io.StringIO("pinctrl-rp1")
                    raise FileNotFoundError(path)

                pcs.lgpio = type("FakeLgpio", (), {"gpio_get_chip_info": lambda self, chip: {"label": "pinctrl-rp1" if chip == 4 else "pinctrl-bcm2835"}, "gpiochip_open": lambda self, chip: chip, "gpiochip_close": lambda self, chip: None})()
                builtins.open = fake_open
                self.assertEqual(pcs._resolve_gpiochip(), 4)
            finally:
                pcs.glob.glob = original_glob
                os.path.exists = original_exists
                builtins.open = original_open
                pcs.lgpio = original_lgpio
        finally:
            if old is None:
                os.environ.pop("MCD_GPIOCHIP", None)
            else:
                os.environ["MCD_GPIOCHIP"] = old


# ------------------------------------------------ real hardware (Raspberry Pi)
@unittest.skipUnless(os.environ.get("MCD_HW_TESTS") == "1",
                     "hardware test: set MCD_HW_TESTS=1 on the Raspberry Pi")
class HardwareSmokeTest(_DriverTestCase):
    MODE = "0"

    def test_buzzer_and_led_pulse(self):
        for pin in (config.GPIO_LED, config.GPIO_BUZZER):
            pcs.PCS_vSetOutputGpio(pin, 1)
            tim.TIM_vDelayMs(150)
            pcs.PCS_vSetOutputGpio(pin, 0)
        self.assertFalse(pcs.PCS_bIsSimulated())

    def test_ultrasonic_single_read(self):
        cm = ultrasonic.US_u16ReadDistanceCm()
        self.assertFalse(ultrasonic.US_bIsSimulated())
        if cm == ultrasonic.US_DISTANCE_INVALID:
            self.skipTest("no valid echo (sensor not connected or no target in 2-400 cm)")
        self.assertTrue(ultrasonic.US_bLastReadValid())
        self.assertTrue(2 <= cm <= 400)

    def test_vibration_inputs(self):
        for pin in config.vibration_pins():
            pcs.PCS_vConfigEdgeCounter(pin)
            self.assertIn(pcs.PCS_sEdgeCounterMode(pin), ("callback", "poll"))
            self.assertIn(pcs.PCS_u8GetGpio(pin), (0, 1))
            pcs.PCS_u16GetEdgeCount(pin, True)

    def test_usb_ports(self):
        found = 0
        for ch in config.usb_channels():
            cfg = config.usb_config(ch)
            if cfg["error"]:
                self.fail(cfg["error"])
            if not os.path.exists(cfg["port"]):
                continue
            usb.USB_vOpen(ch)
            self.assertFalse(usb.USB_bIsSimulated(ch))
            found += 1
        if not found:
            self.skipTest("no USB port connected")


# --------------------------------------------------------------------- run
SIM_TEST_CLASSES = (PcsTests, UsbTests, TimTests, UltrasonicTests, EdgeCounterTests,
                    EdgeSourceHardwarePathTests, PortDetectionTests, AlertChainTests)


def build_sim_suite():
    """Suite without hardware (also used by the web self-test page)."""
    loader = unittest.TestLoader()
    return unittest.TestSuite(loader.loadTestsFromTestCase(c) for c in SIM_TEST_CLASSES)


if __name__ == "__main__":
    unittest.main(verbosity=2)
