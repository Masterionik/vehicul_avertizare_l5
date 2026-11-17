"""SCHED tests: 100 ms / 1 s / 60 s ticks on a fake clock.

Run: MCD_SIMULATE=1 python -m sched.test   (also discovered by `manage.py test`)
"""
import unittest

import sched
from mcd import tim
from mcd.test import FakeClock


class SchedTickTests(unittest.TestCase):
    def setUp(self):
        tim.TIM_vInit()
        self.clk = FakeClock(0.0)
        tim.TIM_vSetClock(self.clk)
        self.addCleanup(sched.SCHED_vStop)
        self.addCleanup(tim.TIM_vInit)

    def test_nothing_fires_before_start(self):
        self.clk.t = 100.0
        self.assertFalse(sched.SCHED_bTick100ms())
        self.assertFalse(sched.SCHED_bTick1s())
        self.assertFalse(sched.SCHED_bTick60s())

    def test_100ms_tick_fires_once_per_period(self):
        sched.SCHED_vStart()
        self.clk.t = 0.099
        self.assertFalse(sched.SCHED_bTick100ms())
        self.clk.t = 0.1
        self.assertTrue(sched.SCHED_bTick100ms())
        self.assertFalse(sched.SCHED_bTick100ms())          # the event is consumed
        self.clk.t = 0.2
        self.assertTrue(sched.SCHED_bTick100ms())

    def test_ticks_are_independent(self):
        sched.SCHED_vStart()
        self.clk.t = 1.0
        self.assertTrue(sched.SCHED_bTick100ms())
        self.assertTrue(sched.SCHED_bTick1s())
        self.assertFalse(sched.SCHED_bTick60s())
        self.assertFalse(sched.SCHED_bTick1s())
        self.clk.t = 60.0
        self.assertTrue(sched.SCHED_bTick60s())
        self.assertFalse(sched.SCHED_bTick60s())

    def test_missed_periods_do_not_burst(self):
        sched.SCHED_vStart()
        self.clk.t = 10.05
        self.assertTrue(sched.SCHED_bTick100ms())
        self.assertFalse(sched.SCHED_bTick100ms())

    def test_stop_and_restart(self):
        sched.SCHED_vStart()
        sched.SCHED_vStop()
        self.clk.t = 5.0
        self.assertFalse(sched.SCHED_bTick1s())
        sched.SCHED_vStart()                                 # periods restart from "now"
        self.clk.t = 5.9
        self.assertFalse(sched.SCHED_bTick1s())
        self.clk.t = 6.0
        self.assertTrue(sched.SCHED_bTick1s())

    def test_start_twice_is_safe(self):
        sched.SCHED_vStart()
        sched.SCHED_vStart()
        self.clk.t = 1.0
        self.assertTrue(sched.SCHED_bTick1s())
        self.assertFalse(sched.SCHED_bTick1s())

    def test_helpers_kept_from_before(self):
        tim.TIM_vStart(tim.TIM_CH_ALERT, 5000, tim.TIM_MODE_ONESHOT)
        self.clk.t = 5.0
        self.assertTrue(sched.scheduler_tick(tim.TIM_CH_ALERT))
        self.assertTrue(sched.alert_expired(100, 100))
        self.assertFalse(sched.alert_expired(100, 99))


if __name__ == "__main__":
    unittest.main(verbosity=2)
