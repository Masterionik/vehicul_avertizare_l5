"""IOC tests: channel routing for buzzer, LED, distance sensor and vibration inputs (simulation).

Run: MCD_SIMULATE=1 python -m ioc.test   (also discovered by `manage.py test`)
"""
import os
import unittest
from unittest import mock

import ioc
from mcd import config, pcs, ultrasonic
from mcd.test import _DriverTestCase


class IocOutputTests(_DriverTestCase):
    def test_buzzer_and_led_chain_reaches_the_gpio_pins(self):
        self.assertEqual(pcs.PCS_u8GetGpio(config.GPIO_BUZZER), 0)
        ioc.IOC_vSetOutputPort(ioc.IOC_CH_BUZZER, 1)
        self.assertEqual(pcs.PCS_u8GetGpio(17), 1)
        self.assertEqual(pcs.PCS_u8GetGpio(27), 0)
        ioc.IOC_vSetOutputPort(ioc.IOC_CH_LED, 1)
        self.assertEqual(pcs.PCS_u8GetGpio(27), 1)
        ioc.IOC_vSetOutputPort(ioc.IOC_CH_BUZZER, 0)
        self.assertEqual((pcs.PCS_u8GetGpio(17), pcs.PCS_u8GetGpio(27)), (0, 1))
        ioc.IOC_vSetOutputPort(ioc.IOC_CH_LED, 0)
        self.assertEqual(pcs.PCS_u8GetGpio(27), 0)

    def test_invalid_values_are_ignored(self):
        ioc.IOC_vSetOutputPort(ioc.IOC_CH_BUZZER, 1)
        ioc.IOC_vSetOutputPort(ioc.IOC_CH_LED, 1)
        for bad in (2, -1, 0.6, 1.0, True, False, None, "1", [1]):
            ioc.IOC_vSetOutputPort(ioc.IOC_CH_BUZZER, bad)
            ioc.IOC_vSetOutputPort(ioc.IOC_CH_LED, bad)
        self.assertEqual((pcs.PCS_u8GetGpio(17), pcs.PCS_u8GetGpio(27)), (1, 1))

    def test_unknown_or_input_channels_are_ignored(self):
        ioc.IOC_vSetOutputPort(ioc.IOC_CH_BUZZER, 1)
        for bad in (0xFFFF, 0, -1, None, "1", [], ioc.IOC_CH_DISTANCE, ioc.IOC_CH_VIBRATION):
            ioc.IOC_vSetOutputPort(bad, 0)
        self.assertEqual(pcs.PCS_u8GetGpio(17), 1)
        self.assertEqual(pcs.PCS_u8GetGpio(27), 0)


class IocDistanceTests(_DriverTestCase):
    def test_distance_and_status(self):
        ultrasonic.US_vSimSetDistanceCm(100)
        self.assertEqual(ioc.IOC_u16GetDistanceCm(), 100)
        self.assertEqual(ioc.IOC_u8GetDistanceStatus(), 1)

    def test_invalid_distance(self):
        for cm in (500, 1, None):
            ultrasonic.US_vSimSetDistanceCm(cm)
            self.assertEqual(ioc.IOC_u16GetDistanceCm(), ioc.IOC_DISTANCE_INVALID)
            self.assertEqual(ioc.IOC_u8GetDistanceStatus(), 0)

    def test_status_is_zero_before_any_measurement(self):
        self.assertEqual(ioc.IOC_u8GetDistanceStatus(), 0)


class IocVibrationTests(_DriverTestCase):
    def setUp(self):
        super().setUp()
        patcher = mock.patch.dict(os.environ, {config.VIB2_ENV: "1"})
        patcher.start()
        self.addCleanup(patcher.stop)
        ioc.IOC_vInit()

    def test_edges_are_summed_over_both_sensors_and_consumed(self):
        pcs.PCS_vSimInjectEdges(22, 3)
        pcs.PCS_vSimInjectEdges(25, 2)
        self.assertEqual(ioc.IOC_u16GetVibrationEdges(), 5)
        self.assertEqual(ioc.IOC_u16GetVibrationEdges(), 0)

    def test_state_follows_the_raw_level(self):
        self.assertEqual(ioc.IOC_u8GetVibrationState(), 0)
        pcs.PCS_vSimSetInput(25, 1)
        self.assertEqual(ioc.IOC_u8GetVibrationState(), 1)
        pcs.PCS_vSimSetInput(25, 0)
        self.assertEqual(ioc.IOC_u8GetVibrationState(), 0)

    def test_rising_edge_of_the_input_is_counted(self):
        pcs.PCS_vSimSetInput(22, 1)
        self.assertEqual(ioc.IOC_u16GetVibrationEdges(), 1)

    def test_second_sensor_can_be_disabled(self):
        os.environ[config.VIB2_ENV] = "0"
        pcs.PCS_vSimInjectEdges(22, 2)
        pcs.PCS_vSimInjectEdges(25, 4)           # no counter on pin 25 when the sensor is not fitted
        self.assertEqual(ioc.IOC_u16GetVibrationEdges(), 2)

    def test_works_without_explicit_init(self):
        ioc.IOC_vDeinit()
        pcs.PCS_vInit()
        pcs.PCS_vConfigEdgeCounter(22)
        pcs.PCS_vSimInjectEdges(22, 1)
        self.assertEqual(ioc.IOC_u16GetVibrationEdges(), 1)


class IocLifecycleTests(_DriverTestCase):
    def test_deinit_turns_buzzer_and_led_off(self):
        ioc.IOC_vInit()
        ioc.IOC_vSetOutputPort(ioc.IOC_CH_BUZZER, 1)
        ioc.IOC_vSetOutputPort(ioc.IOC_CH_LED, 1)
        ioc.IOC_vDeinit()
        self.assertEqual((pcs.PCS_u8GetGpio(17), pcs.PCS_u8GetGpio(27)), (0, 0))

    def test_init_twice_is_safe_and_keeps_counters(self):
        ioc.IOC_vInit()
        pcs.PCS_vSimInjectEdges(22, 2)
        ioc.IOC_vInit()
        self.assertEqual(ioc.IOC_u16GetVibrationEdges(), 2)

    def test_deinit_resets_the_distance_sensor(self):
        ultrasonic.US_vSimSetDistanceCm(50)
        ioc.IOC_vInit()
        ioc.IOC_vDeinit()
        self.assertEqual(ioc.IOC_u16GetDistanceCm(), 300)


if __name__ == "__main__":
    unittest.main(verbosity=2)
