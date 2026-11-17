"""Tests of the application layer (PRX, IMP, ALR, RPT, NODE). Pure logic runs on explicit fake times."""
import os
import unittest
from unittest import mock

import rte
from app import alr, imp, node, prx, rpt
from mcd import config, pcs, tim, ultrasonic, usb

INVALID = rte.RTE_DISTANCE_INVALID


def feed(p, distance, count, t0=0, step=100):
    """Feed `count` identical samples; returns (level, next time)."""
    level = p.level
    for i in range(count):
        level = p.update(distance, t0 + i * step)
    return level, t0 + count * step


class ProximityTests(unittest.TestCase):
    def test_raw_levels_at_exact_boundaries(self):
        raw = prx.PRX_u8LevelForDistance
        self.assertEqual([raw(151), raw(150), raw(81), raw(80), raw(31), raw(30), raw(2)], [0, 1, 1, 2, 2, 3, 3])

    def test_level_changes_only_after_three_consecutive_samples(self):
        p = prx.PRX()
        self.assertEqual(feed(p, 100, 2)[0], 0)
        self.assertEqual(feed(p, 100, 1, t0=200)[0], 1)

    def test_a_single_outlier_resets_the_count(self):
        p = prx.PRX()
        feed(p, 100, 2)
        p.update(300, 200)
        self.assertEqual(feed(p, 100, 2, t0=300)[0], 0)
        self.assertEqual(feed(p, 100, 1, t0=500)[0], 1)

    def test_boundary_distances_select_the_level(self):
        for distance, level in ((150, 1), (151, 0), (80, 2), (30, 3)):
            p = prx.PRX()
            self.assertEqual(feed(p, distance, 3)[0], level, distance)

    def test_hysteresis_when_the_level_decreases(self):
        p = prx.PRX()
        feed(p, 80, 3)
        self.assertEqual(p.level, 2)
        self.assertEqual(feed(p, 84, 10)[0], 2)                      # inside the 5 cm margin: stays
        self.assertEqual(feed(p, 86, 3, t0=2000)[0], 1)              # beyond the margin: drops after 3 samples

    def test_level_rises_without_hysteresis(self):
        p = prx.PRX()
        feed(p, 100, 3)
        self.assertEqual(feed(p, 80, 3, t0=1000)[0], 2)

    def test_invalid_reading_keeps_level_then_falls_back_with_fault(self):
        p = prx.PRX()
        feed(p, 20, 3)
        self.assertEqual(p.level, 3)
        p.update(INVALID, 1000)
        self.assertEqual((p.level, p.fault), (3, False))
        p.update(INVALID, 1999)
        self.assertEqual((p.level, p.fault), (3, False))
        p.update(INVALID, 2000)
        self.assertEqual((p.level, p.fault), (0, True))

    def test_valid_reading_clears_fault(self):
        p = prx.PRX()
        p.update(INVALID, 0)
        p.update(INVALID, 1000)
        self.assertTrue(p.fault)
        p.update(200, 1100)
        self.assertFalse(p.fault)

    def test_short_invalid_gap_does_not_reset_the_hold_timer_forever(self):
        p = prx.PRX()
        feed(p, 20, 3)
        p.update(INVALID, 0)
        p.update(100, 100)                                           # a valid sample restarts the hold time
        p.update(INVALID, 200)
        p.update(INVALID, 1100)
        self.assertFalse(p.fault)


class ImpactTests(unittest.TestCase):
    def test_three_edges_inside_the_window_declare_an_impact(self):
        d = imp.IMP()
        self.assertFalse(d.update(1, 0))
        self.assertFalse(d.update(1, 100))
        self.assertTrue(d.update(1, 300))                            # exactly 300 ms after the first edge
        self.assertEqual(d.total, 1)

    def test_edges_outside_the_window_do_not_add_up(self):
        d = imp.IMP()
        d.update(1, 0)
        d.update(1, 100)
        self.assertFalse(d.update(1, 301))

    def test_one_step_with_a_burst_of_edges(self):
        self.assertTrue(imp.IMP().update(5, 0))

    def test_too_few_edges_and_zero_edges_do_nothing(self):
        d = imp.IMP()
        self.assertFalse(d.update(2, 0))
        self.assertFalse(d.update(0, 100))
        self.assertEqual(d.total, 0)

    def test_lockout_blocks_repeats_for_three_seconds(self):
        d = imp.IMP()
        self.assertTrue(d.update(3, 0))
        self.assertFalse(d.update(5, 1000))
        self.assertFalse(d.update(5, 2999))
        self.assertTrue(d.update(3, 3000))
        self.assertEqual(d.total, 2)

    def test_edges_during_lockout_are_not_remembered(self):
        d = imp.IMP()
        d.update(3, 0)
        d.update(2, 2900)
        self.assertFalse(d.update(1, 3000))

    def test_flag_is_held_for_two_seconds(self):
        d = imp.IMP()
        self.assertFalse(d.active(0))
        d.update(3, 1000)
        self.assertTrue(d.active(1000))
        self.assertTrue(d.active(2999))
        self.assertFalse(d.active(3000))


class AlertPatternTests(unittest.TestCase):
    def run_pattern(self, level, duration, step=50, **kw):
        m = alr.AlertManager()
        return [(t, m.update(t, level, kw.get("impact", 0), 50)) for t in range(0, duration, step)]

    def test_idle_is_silent(self):
        for _, a in self.run_pattern(0, 2000):
            self.assertEqual((a["buzzer"], a["led"], a["send"]), (0, 0, []))

    def test_level_1_pattern_200_on_800_off(self):
        out = {t: a["buzzer"] for t, a in self.run_pattern(1, 2100)}
        self.assertEqual([out[0], out[150], out[200], out[950], out[1000], out[1150], out[1200]], [1, 1, 0, 0, 1, 1, 0])

    def test_level_2_pattern_200_on_300_off(self):
        out = {t: a["buzzer"] for t, a in self.run_pattern(2, 1100)}
        self.assertEqual([out[0], out[150], out[200], out[450], out[500], out[650], out[700]], [1, 1, 0, 0, 1, 1, 0])

    def test_level_3_is_continuous(self):
        self.assertTrue(all(a["buzzer"] == 1 and a["led"] == 1 for _, a in self.run_pattern(3, 3000)))

    def test_led_follows_the_buzzer_pattern_for_local_levels(self):
        for _, a in self.run_pattern(2, 1000):
            self.assertEqual(a["buzzer"], a["led"])

    def test_pattern_restarts_when_the_level_changes(self):
        m = alr.AlertManager()
        m.update(0, 1, 0, 50)
        self.assertEqual(m.update(500, 2, 0, 50)["buzzer"], 1)       # new level starts with an "on" phase

    def test_impact_is_two_seconds_of_buzzer_with_a_flashing_led(self):
        m = alr.AlertManager()
        buzzer, led = [], []
        for t in range(0, 2500, 100):
            a = m.update(t, 0, 1 if t < 2000 else 0, 50)
            buzzer.append(a["buzzer"])
            led.append(a["led"])
        self.assertEqual(buzzer[:20], [1] * 20)
        self.assertEqual(buzzer[20:], [0] * 5)
        self.assertEqual(led[:6], [1, 0, 1, 0, 1, 0])
        self.assertEqual(led[20:], [0] * 5)

    def test_impact_ends_after_two_seconds_even_if_the_flag_stays_high(self):
        m = alr.AlertManager()
        m.update(0, 0, 1, 50)
        self.assertEqual(m.update(2000, 0, 1, 50)["buzzer"], 0)

    def test_remote_alert_is_two_short_beeps_and_five_seconds_of_led(self):
        m = alr.AlertManager()
        m.on_remote(1000, {"node": 7, "kind": 1, "level": 2, "distance_cm": 40, "seq": 1}, "mesh")
        beeps = [m.update(1000 + t, 0, 0, INVALID)["buzzer"] for t in range(0, 1000, 50)]
        self.assertEqual(beeps[:3], [1, 1, 1])                       # 0-150 ms on
        self.assertEqual(beeps[3:6], [0, 0, 0])
        self.assertEqual(beeps[6:9], [1, 1, 1])                      # second beep 300-450 ms
        self.assertEqual(beeps[9:], [0] * 11)
        self.assertEqual(m.update(5999, 0, 0, INVALID)["led"], 1)
        self.assertEqual(m.update(6000, 0, 0, INVALID)["led"], 0)
        self.assertEqual(m.remote_level(5999), 2)
        self.assertEqual(m.remote_level(6000), 0)
        self.assertEqual(m.last_remote["radio"], "mesh")

    def test_remote_clear_message_does_not_beep(self):
        m = alr.AlertManager()
        m.on_remote(0, {"node": 7, "kind": 1, "level": 0, "distance_cm": 300, "seq": 1}, "zigbee")
        self.assertEqual(m.update(0, 0, 0, 300), {"buzzer": 0, "led": 0, "send": []})
        self.assertIsNotNone(m.last_remote)

    def test_remote_impact_always_alerts(self):
        m = alr.AlertManager()
        m.on_remote(0, {"node": 7, "kind": 2, "level": 0, "distance_cm": 0, "seq": 1}, "zigbee")
        self.assertEqual(m.update(0, 0, 0, 300)["buzzer"], 1)

    def test_remote_alert_does_not_silence_a_local_danger(self):
        m = alr.AlertManager()
        m.on_remote(0, {"node": 7, "kind": 1, "level": 1, "distance_cm": 90, "seq": 1}, "zigbee")
        self.assertEqual(m.update(200, 3, 0, 20)["buzzer"], 1)


class AlertRateLimitTests(unittest.TestCase):
    def sends(self, level, impact, duration, radio):
        m = alr.AlertManager()
        times = []
        for t in range(0, duration, 100):
            if any(s[0] == radio for s in m.update(t, level, impact, 40)["send"]):
                times.append(t)
        return times

    def test_zigbee_once_per_second_mesh_once_per_five(self):
        self.assertEqual(self.sends(2, 0, 5200, "zigbee"), [0, 1000, 2000, 3000, 4000, 5000])
        self.assertEqual(self.sends(3, 0, 11000, "mesh"), [0, 5000, 10000])

    def test_nothing_is_sent_below_level_2_without_impact(self):
        self.assertEqual(self.sends(1, 0, 3000, "zigbee"), [])
        self.assertEqual(self.sends(0, 0, 3000, "mesh"), [])

    def test_impact_sends_kind_2_with_level_3(self):
        m = alr.AlertManager()
        sent = m.update(0, 0, 1, 25)["send"]
        self.assertEqual(sorted(sent), [("mesh", 2, 3, 25), ("zigbee", 2, 3, 25)])

    def test_proximity_alert_carries_kind_level_and_distance(self):
        m = alr.AlertManager()
        self.assertIn(("zigbee", 1, 2, 70), m.update(0, 2, 0, 70)["send"])


class ReportTests(unittest.TestCase):
    def test_counters_and_reset(self):
        r = rpt.RPT()
        r.add_sample(100, 1, True)
        r.add_sample(40, 2, True)
        r.add_sample(INVALID, 0, False)
        r.add_impact()
        r.add_fault()
        r.add_tx("zigbee")
        r.add_tx("zigbee")
        r.add_tx("mesh")
        r.add_rx("mesh")
        r.add_rx("unknown")                                          # ignored
        snap = r.snapshot(reset=True)
        self.assertEqual(snap["samples"], 3)
        self.assertEqual(snap["valid_samples"], 2)
        self.assertEqual(snap["max_level"], 2)
        self.assertEqual(snap["min_distance_cm"], 40)
        self.assertEqual((snap["impacts"], snap["sensor_faults"]), (1, 1))
        self.assertEqual(snap["tx"], {"zigbee": 2, "mesh": 1})
        self.assertEqual(snap["rx"], {"zigbee": 0, "mesh": 1})
        again = r.snapshot()
        self.assertEqual((again["samples"], again["impacts"], again["min_distance_cm"]), (0, 0, None))

    def test_snapshot_without_reset_keeps_counting(self):
        r = rpt.RPT()
        r.add_impact()
        r.snapshot()
        self.assertEqual(r.snapshot()["impacts"], 1)


class _NodeTestCase(unittest.TestCase):
    """Real RTE in simulation, with outputs checked on the simulated GPIO pins."""
    _ENV = {"MCD_SIMULATE": "1", "MESH_HMAC_KEY": "app-test-key", "MESH_NODE_ID": "0x01020304"}
    _CLEAR = ("ZIGBEE_PORT", "MESHTASTIC_PORT", "MCD_ZIGBEE_PORT", "MCD_MESHTASTIC_PORT", "MCD_SERIAL_BY_ID_DIR")

    @staticmethod
    def _reset():
        node.NODE_vStop()
        ultrasonic.US_vDeinit()
        pcs.PCS_vDeinit()
        usb.USB_vCloseAll()
        tim.TIM_vInit()

    def setUp(self):
        saved = {k: os.environ.get(k) for k in list(self._ENV) + list(self._CLEAR)}

        def restore():
            self._reset()
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

        self.addCleanup(restore)
        for key in self._CLEAR:
            os.environ.pop(key, None)
        os.environ.update(self._ENV)
        self._reset()
        rte.RTE_vInit()
        node._runtime = node.NodeRuntime()

    def pins(self):
        return pcs.PCS_u8GetGpio(config.GPIO_BUZZER), pcs.PCS_u8GetGpio(config.GPIO_LED)


class NodeStepTests(_NodeTestCase):
    def test_distance_drives_level_and_outputs_through_the_rte(self):
        ultrasonic.US_vSimSetDistanceCm(20)
        for t in range(0, 400, 100):
            node.NODE_vStep(t)
        status = node.NODE_tGetStatus()
        self.assertEqual((status["distance_cm"], status["level"]), (20, 3))
        self.assertEqual(rte.RTE_u16ReadDistanceCm(), 20)
        self.assertEqual(rte.RTE_u8ReadAlertLevel(), 3)
        self.assertEqual(self.pins(), (1, 1))

    def test_clear_distance_keeps_outputs_off_and_sends_nothing(self):
        ultrasonic.US_vSimSetDistanceCm(300)
        for t in range(0, 1000, 100):
            node.NODE_vStep(t)
        self.assertEqual(self.pins(), (0, 0))
        self.assertEqual(node._runtime.rpt.snapshot()["tx"], {"zigbee": 0, "mesh": 0})

    def test_vibration_edges_raise_the_impact_flag_and_the_buzzer(self):
        pcs.PCS_vSimInjectEdges(config.GPIO_VIB[0], 4)
        node.NODE_vStep(0)
        self.assertEqual(rte.RTE_u8ReadImpact(), 1)
        self.assertEqual(node.NODE_tGetStatus()["impact"], 1)
        self.assertEqual(self.pins(), (1, 1))
        node.NODE_vStep(2100)
        self.assertEqual(rte.RTE_u8ReadImpact(), 0)
        self.assertEqual(self.pins(), (0, 0))
        self.assertEqual(node._runtime.rpt.snapshot()["impacts"], 1)

    def test_warning_sends_over_both_radios_and_counts_tx(self):
        ultrasonic.US_vSimSetDistanceCm(60)
        for t in range(0, 6100, 100):
            node.NODE_vStep(t)
        tx = node._runtime.rpt.snapshot()["tx"]
        self.assertEqual(tx["zigbee"], 6)                            # level 2 from t=200 ms, then every 1 s
        self.assertEqual(tx["mesh"], 2)

    def test_no_echo_falls_back_to_level_0_with_sensor_fault(self):
        ultrasonic.US_vSimSetDistanceCm(20)
        for t in range(0, 300, 100):
            node.NODE_vStep(t)
        self.assertEqual(node.NODE_tGetStatus()["level"], 3)
        ultrasonic.US_vSimSetDistanceCm(500)                         # out of range -> invalid
        for t in range(300, 1500, 100):
            node.NODE_vStep(t)
        status = node.NODE_tGetStatus()
        self.assertEqual((status["level"], status["sensor_fault"], status["distance_cm"]), (0, True, None))
        self.assertEqual(self.pins(), (0, 0))
        self.assertEqual(node._runtime.rpt.snapshot()["sensor_faults"], 1)

    def test_remote_alert_from_either_radio_is_handled(self):
        for radio, name in (("zigbee", "RTE_tGetZigbeeAlert"), ("mesh", "RTE_tGetMeshAlert")):
            m = alr.AlertManager()
            alert = {"node": 9, "kind": 1, "level": 2, "distance_cm": 40, "seq": 3}
            seen = []
            with mock.patch.object(alr, name, side_effect=[alert, None]), \
                    mock.patch.object(alr, "RTE_tGetMeshAlert" if radio == "zigbee" else "RTE_tGetZigbeeAlert",
                                      return_value=None):
                alr.ALR_vStep(m, 0, 0, 0, 300, on_rx=lambda r, a: seen.append((r, a["node"])))
            self.assertEqual(seen, [(radio, 9)])
            self.assertEqual(m.last_remote["radio"], radio)
            self.assertEqual(rte.RTE_u8ReadRemoteAlertLevel(), 2)
            self.assertEqual(self.pins(), (1, 1))

    def test_remote_alert_over_the_simulated_link_reaches_the_dashboard_status(self):
        with mock.patch.object(alr, "RTE_tGetZigbeeAlert",
                               side_effect=[{"node": 5, "kind": 2, "level": 3, "distance_cm": 0, "seq": 1}, None]):
            node.NODE_vStep(0)
        status = node.NODE_tGetStatus()
        self.assertEqual(status["last_remote_alert"]["node"], 5)
        self.assertEqual(node._runtime.rpt.snapshot()["rx"]["zigbee"], 1)
        self.assertTrue(any("remote alert" in e["text"] for e in node.NODE_lGetEvents()))

    def test_report_is_published_on_the_60s_tick(self):
        clock = [0.0]
        tim.TIM_vSetClock(lambda: clock[0])
        rte.RTE_vDeinit()
        rte.RTE_vInit()
        ultrasonic.US_vSimSetDistanceCm(100)
        node.NODE_vStep(0)
        self.assertIsNone(node.NODE_tGetStatus()["report"])
        clock[0] = 61.0
        node.NODE_vStep(100)
        report = node.NODE_tGetStatus()["report"]
        self.assertEqual(report["samples"], 2)


class ShutdownTests(_NodeTestCase):
    def test_shutdown_forces_buzzer_and_led_off(self):
        rte.RTE_vSetBuzzer(1)
        rte.RTE_vSetLed(1)
        alr.ALR_vShutdown()
        self.assertEqual(self.pins(), (0, 0))

    def test_stopping_the_runtime_thread_turns_outputs_off(self):
        ultrasonic.US_vSimSetDistanceCm(20)
        node.NODE_vStart()
        self.assertTrue(node.NODE_bIsRunning())
        for _ in range(100):
            if node.NODE_tGetStatus()["level"] == 3 and self.pins() == (1, 1):
                break
            tim.TIM_vDelayMs(20)
        self.assertEqual(self.pins(), (1, 1))
        node.NODE_vStop()
        self.assertFalse(node.NODE_bIsRunning())
        self.assertEqual(self.pins(), (0, 0))

    def test_step_error_never_leaves_outputs_on(self):
        rte.RTE_vSetBuzzer(1)
        with mock.patch.object(node, "NODE_vStep", side_effect=RuntimeError("boom")):
            import threading
            stop = threading.Event()
            thread = threading.Thread(target=node._loop, args=(stop,))
            thread.start()
            tim.TIM_vDelayMs(300)
            stop.set()
            thread.join(2)
        self.assertEqual(self.pins(), (0, 0))

    def test_start_twice_keeps_one_thread(self):
        node.NODE_vStart()
        first = node._thread
        node.NODE_vStart()
        self.assertIs(node._thread, first)

    def test_alert_active_flag(self):
        self.assertFalse(node.NODE_bAlertActive())
        node._runtime.alr.update(0, 2, 0, 60)
        self.assertTrue(node.NODE_bAlertActive())


class PulseAndStatusTests(_NodeTestCase):
    def test_pulse_switches_the_output_from_the_runtime_step_and_expires(self):
        self.assertTrue(node.NODE_bRequestPulse("buzzer", 500, now_ms=0))
        node.NODE_vStep(100)
        self.assertEqual(self.pins(), (1, 0))
        node.NODE_vStep(499)
        self.assertEqual(self.pins(), (1, 0))
        node.NODE_vStep(500)
        self.assertEqual(self.pins(), (0, 0))

    def test_led_pulse_is_independent_of_the_buzzer(self):
        self.assertTrue(node.NODE_bRequestPulse("led", 300, now_ms=0))
        node.NODE_vStep(100)
        self.assertEqual(self.pins(), (0, 1))

    def test_invalid_pulse_requests_are_refused(self):
        for output, duration in (("motor", 100), ("buzzer", 0), ("buzzer", 2001), ("led", -1),
                                 ("led", 1.5), ("led", True), ("buzzer", None)):
            self.assertFalse(node.NODE_bRequestPulse(output, duration, now_ms=0), (output, duration))
        node.NODE_vStep(100)
        self.assertEqual(self.pins(), (0, 0))

    def test_pulse_is_refused_while_an_alert_is_active_and_dropped_if_one_starts(self):
        self.assertTrue(node.NODE_bRequestPulse("led", 2000, now_ms=0))
        ultrasonic.US_vSimSetDistanceCm(60)
        for t in range(0, 400, 100):
            node.NODE_vStep(t)
        self.assertTrue(node.NODE_bAlertActive())
        self.assertFalse(node.NODE_bRequestPulse("buzzer", 500, now_ms=400))
        self.assertEqual(node._runtime.pulses, {})

    def test_clear_pulses_forces_outputs_off(self):
        node.NODE_bRequestPulse("buzzer", 2000, now_ms=0)
        node.NODE_vStep(100)
        node.NODE_vClearPulses()
        self.assertEqual(self.pins(), (0, 0))

    def test_remote_alert_stops_counting_as_active_after_five_seconds(self):
        m = alr.AlertManager()
        m.on_remote(0, {"node": 1, "kind": 1, "level": 2, "distance_cm": 40, "seq": 1}, "mesh")
        m.update(100, 0, 0, 300)
        self.assertTrue(m.active)
        m.update(5000, 0, 0, 300)
        self.assertFalse(m.active)

    def test_status_reports_edges_and_live_counters(self):
        pcs.PCS_vSimInjectEdges(config.GPIO_VIB[0], 2)
        node.NODE_vStep(0)
        status = node.NODE_tGetStatus()
        self.assertEqual(status["vibration_edges_total"], 2)
        self.assertEqual(status["impacts_total"], 0)
        self.assertEqual(status["counters"]["samples"], 1)
        self.assertEqual(status["steps"], 1)


if __name__ == "__main__":
    unittest.main()
