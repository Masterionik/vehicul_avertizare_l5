"""Tests of the web layer.

* ``ApiTests`` run the Django-independent logic in ``web.api`` against the real RTE in simulation.
* ``RuntimeStartTests`` and ``EnglishOnlyTests`` need no Django either.
* ``DjangoViewTests`` use the Django test client (pages, JSON schema, POST-only, CSRF); they run under
  ``MCD_SIMULATE=1 python manage.py test`` and are skipped when Django is not installed.
"""
import os
import re
import tempfile
import time
import unittest
from unittest import mock

import rte
from app import node
from mcd import config, pcs, tim, ultrasonic, usb
from sdc.registry import DeviceRegistry
from sim import devices, vehicles
from web import api
from web.runtime import should_start_runtime

try:
    import django  # noqa: F401
    from django.test import SimpleTestCase
    from django.urls import reverse
    HAVE_DJANGO = bool(os.environ.get("DJANGO_SETTINGS_MODULE"))
except ImportError:                                           # pragma: no cover
    HAVE_DJANGO = False
    SimpleTestCase = unittest.TestCase

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
STATE_KEYS = {"time", "running", "distance_cm", "sensor_fault", "level", "buzzer", "led", "impact",
              "remote_level", "last_remote_alert", "mesh", "zigbee", "simulation", "counters",
              "vibration_edges_total", "impacts_total", "report", "events"}
_ENV = {"MCD_SIMULATE": "1", "MESH_HMAC_KEY": "web-test-key", "MESH_NODE_ID": "0x01020304"}
_CLEAR = ("ZIGBEE_PORT", "MESHTASTIC_PORT", "MCD_ZIGBEE_PORT", "MCD_MESHTASTIC_PORT", "MCD_SERIAL_BY_ID_DIR",
          "SIM_V2_PORT", "SIM_V2_HOST", "SIM_V3_PORT", "SIM_V3_HOST")


def wait_until(predicate, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def reset_world():
    node.NODE_vStop()
    vehicles.reset_vehicles()
    ultrasonic.US_vDeinit()
    pcs.PCS_vDeinit()
    usb.USB_vCloseAll()
    tim.TIM_vInit()


class _SimCase(unittest.TestCase):
    start_runtime = True

    def setUp(self):
        saved = {k: os.environ.get(k) for k in list(_ENV) + list(_CLEAR)}
        tmp = tempfile.TemporaryDirectory()

        def restore():
            reset_world()
            devices.set_registry(None)
            tmp.cleanup()
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

        self.addCleanup(restore)
        for key in _CLEAR:
            os.environ.pop(key, None)
        os.environ.update(_ENV)
        reset_world()
        devices.set_registry(DeviceRegistry(os.path.join(tmp.name, "devices.json")))
        if self.start_runtime:
            node.NODE_vStart()

    def pins(self):
        return pcs.PCS_u8GetGpio(config.GPIO_BUZZER), pcs.PCS_u8GetGpio(config.GPIO_LED)

    def set_distance_and_wait_level(self, distance, level):
        self.assertEqual(api.sim_distance(distance)[0], 200)
        self.assertTrue(wait_until(lambda: api.state()["level"] == level), "level %d not reached" % level)


class ApiParsingTests(unittest.TestCase):
    def test_parse_int_accepts_only_real_ints_and_digit_strings(self):
        self.assertEqual([api.parse_int(v) for v in (5, "7", " 8 ", "-1")], [5, 7, 8, -1])
        for bad in (True, 1.5, None, "x", "1.5", "", [1]):
            self.assertIsNone(api.parse_int(bad), bad)

    def test_parse_confirm(self):
        self.assertTrue(all(api.parse_confirm(v) for v in (True, "true", "1", "YES")))
        self.assertFalse(any(api.parse_confirm(v) for v in (False, "false", "0", None, 1, "")))


class StateTests(_SimCase):
    def test_state_schema_and_simulation_flags(self):
        s = api.state()
        self.assertEqual(set(s), STATE_KEYS)
        self.assertEqual(s["simulation"], {"sensors": True, "meshtastic": True, "zigbee": True, "any": True})
        self.assertTrue(s["mesh"]["connected"] and s["zigbee"]["connected"])
        self.assertIs(s["mesh"]["simulated"], True)
        self.assertTrue(s["running"])

    def test_state_without_runtime_or_rte_does_not_crash(self):
        reset_world()
        s = api.state()
        self.assertFalse(s["running"])
        self.assertFalse(s["mesh"]["connected"])
        self.assertEqual(s["level"], 0)

    def test_simulated_distance_is_reflected_in_the_state(self):
        self.set_distance_and_wait_level(100, 1)
        self.assertEqual(api.state()["distance_cm"], 100)
        self.set_distance_and_wait_level(25, 3)
        self.assertEqual(api.state()["buzzer"], 1)

    def test_distance_read_returns_the_latest_measurement(self):
        api.sim_distance(77)
        self.assertTrue(wait_until(lambda: api.distance_read()[1]["distance_cm"] == 77))
        self.assertTrue(api.distance_read()[1]["valid"])
        api.sim_distance(0)
        self.assertTrue(wait_until(lambda: api.distance_read()[1]["valid"] is False))

    def test_no_demo_data_before_any_measurement(self):
        reset_world()
        s = api.state()
        self.assertIsNone(s["distance_cm"])
        self.assertIsNone(s["last_remote_alert"])
        self.assertEqual(s["events"], [])


class ActuatorTests(_SimCase):
    def test_pulse_switches_on_and_turns_itself_off(self):
        status, body = api.pulse("buzzer", 300)
        self.assertEqual((status, body["duration_ms"]), (200, 300))
        self.assertTrue(wait_until(lambda: self.pins() == (1, 0)))
        self.assertTrue(wait_until(lambda: self.pins() == (0, 0), timeout=1.5))

    def test_led_pulse_default_duration(self):
        status, body = api.pulse("led")
        self.assertEqual((status, body["duration_ms"]), (200, 500))
        self.assertTrue(wait_until(lambda: self.pins() == (0, 1)))
        self.assertTrue(wait_until(lambda: self.pins() == (0, 0), timeout=2.0))

    def test_pulse_is_never_longer_than_two_seconds(self):
        for duration in (2001, 0, -5, "abc", 1.5, True):
            self.assertEqual(api.pulse("buzzer", duration)[0], 400, duration)
        self.assertEqual(api.pulse("buzzer", 2000)[0], 200)
        self.assertEqual(api.pulse("buzzer", "300")[0], 200)

    def test_unknown_output_is_rejected(self):
        self.assertEqual(api.pulse("motor", 100)[0], 400)
        self.assertEqual(self.pins(), (0, 0))

    def test_pulse_is_refused_while_a_real_alert_is_active(self):
        self.set_distance_and_wait_level(20, 3)
        status, body = api.pulse("led", 300)
        self.assertEqual(status, 409)
        self.assertIn("alert", body["error"])

    def test_pulse_needs_the_runtime(self):
        reset_world()
        self.assertEqual(api.pulse("buzzer", 100)[0], 503)

    def test_stop_all_cancels_a_pulse(self):
        api.pulse("buzzer", 2000)
        self.assertTrue(wait_until(lambda: self.pins()[0] == 1))
        self.assertEqual(api.outputs_off()[0], 200)
        self.assertTrue(wait_until(lambda: self.pins() == (0, 0), timeout=1.0))
        time.sleep(0.3)
        self.assertEqual(self.pins(), (0, 0))


class SimulationApiTests(_SimCase):
    def test_distance_validation(self):
        for bad in (-1, 1001, "x", 1.5, None, True):
            self.assertEqual(api.sim_distance(bad)[0], 400, bad)
        self.assertEqual(api.sim_distance(0)[0], 200)
        self.assertEqual(api.sim_distance("55")[0], 200)

    def test_vibration_validation_and_impact(self):
        for bad in (0, 51, "x", None, False):
            self.assertEqual(api.sim_vibration(bad)[0], 400, bad)
        self.assertEqual(api.sim_vibration(4)[0], 200)
        self.assertTrue(wait_until(lambda: api.state()["impact"] == 1))
        s = api.state()
        self.assertGreaterEqual(s["vibration_edges_total"], 4)
        self.assertEqual(s["impacts_total"], 1)
        self.assertTrue(wait_until(lambda: self.pins() == (1, 1)) or self.pins()[0] == 1)

    def test_simulation_hooks_refuse_real_hardware(self):
        from rte import sim_support
        with mock.patch.object(sim_support, "IOC_bIsSimulated", return_value=False):
            self.assertEqual(api.sim_distance(50)[0], 403)
            self.assertEqual(api.sim_vibration(3)[0], 403)

    def test_scripted_remote_alert_reaches_the_dashboard_over_both_radios(self):
        for radio in ("mesh", "zigbee"):
            status, body = api.sim_remote_alert("V2", "warning", radio)
            self.assertEqual(status, 200, body)
            self.assertTrue(body["simulated"] and body["delivered"])
            self.assertTrue(wait_until(lambda: (api.state()["last_remote_alert"] or {}).get("radio") == radio))
        s = api.state()
        self.assertEqual((s["last_remote_alert"]["level"], s["last_remote_alert"]["distance_cm"]), (2, 40))
        self.assertGreater(s["remote_level"], 0)
        self.assertEqual(s["counters"]["rx"]["mesh"] + s["counters"]["rx"]["zigbee"], 2)

    def test_remote_impact_from_v3(self):
        self.assertEqual(api.sim_remote_alert("V3", "impact", "mesh")[0], 200)
        self.assertTrue(wait_until(lambda: (api.state()["last_remote_alert"] or {}).get("kind") == 2))

    def test_remote_alert_validation(self):
        self.assertEqual(api.sim_remote_alert("V9", "warning")[0], 400)
        self.assertEqual(api.sim_remote_alert("V2", "boom")[0], 400)
        self.assertEqual(api.sim_remote_alert("V2", "warning", "wifi")[0], 400)

    def test_repeated_alerts_are_all_accepted_by_the_replay_guard(self):
        for _ in range(3):
            self.assertEqual(api.sim_remote_alert("V2", "caution")[0], 200)
        self.assertTrue(wait_until(lambda: api.state()["counters"]["rx"]["mesh"] == 3))


class ComponentTestsApi(_SimCase):
    def test_all_component_tests_pass_in_simulation(self):
        status, body = api.tests_run("all")
        self.assertEqual(status, 200)
        results = {r["name"]: r for r in body["results"]}
        self.assertEqual(set(results), set(api.TESTS))
        for name, result in results.items():
            self.assertEqual(result["status"], "PASS", (name, result))
        self.assertIn("SIMULATED", results["meshtastic"]["detail"])
        self.assertIn("SIMULATED", results["zigbee"]["detail"])

    def test_ultrasonic_test_fails_without_an_echo(self):
        api.sim_distance(0)
        self.assertTrue(wait_until(lambda: api.distance_read()[1]["valid"] is False))
        result = api.tests_run("ultrasonic")[1]["results"][0]
        self.assertEqual(result["status"], "FAIL")

    def test_buzzer_test_is_skipped_during_a_real_alert(self):
        self.set_distance_and_wait_level(20, 3)
        self.assertEqual(api.tests_run("buzzer_led")[1]["results"][0]["status"], "SKIP")

    def test_tests_need_the_runtime(self):
        reset_world()
        self.assertEqual(api.tests_run("buzzer_led")[1]["results"][0]["status"], "FAIL")

    def test_unknown_test(self):
        self.assertEqual(api.tests_run("nope")[0], 404)
        self.assertEqual(api.tests_list()[1]["tests"], list(api.TESTS))


class DeviceApiTests(_SimCase):
    def test_devices_state_lists_radios_vehicles_and_log(self):
        status, body = api.devices_state()
        self.assertEqual(status, 200)
        self.assertEqual(set(body), {"radios", "vehicles", "devices", "log"})
        self.assertEqual([v["name"] for v in body["vehicles"]], ["V2", "V3"])
        self.assertTrue(all(v["simulated"] for v in body["vehicles"]))
        self.assertTrue(body["radios"]["zigbee"]["simulated"])
        self.assertEqual(body["devices"], [])

    def test_add_tcp_device_validation(self):
        for host in ("", "bad host", "a/b"):
            self.assertEqual(api.devices_add_tcp(host)[0], 400, host)
        self.assertEqual(api.devices_add_tcp("10.0.0.5", "99999")[0], 400)
        self.assertEqual(api.devices_add_tcp("10.0.0.5", "abc")[0], 400)
        status, body = api.devices_add_tcp("10.0.0.5")
        self.assertEqual((status, body["device"]["address"]), (200, "10.0.0.5:4403"))
        self.assertEqual(len(api.devices_state()[1]["devices"]), 1)

    def test_destructive_actions_need_confirmation_and_are_logged(self):
        device_id = api.devices_add_tcp("10.0.0.6")[1]["device"]["id"]
        self.assertEqual(api.device_action(device_id, "disconnect")[0], 400)
        self.assertEqual(api.device_action(device_id, "remove")[0], 400)
        self.assertEqual(len(api.devices_state()[1]["devices"]), 1)
        self.assertEqual(api.device_action(device_id, "remove", confirm=True)[0], 200)
        self.assertEqual(api.devices_state()[1]["devices"], [])
        log = api.devices_state()[1]["log"]
        self.assertEqual((log[-1]["action"], log[-1]["ok"]), ("remove", True))

    def test_unknown_device_and_action(self):
        self.assertEqual(api.device_action("nope", "connect")[0], 404)
        self.assertEqual(api.device_action("nope", "explode")[0], 404)

    def test_sim_device_can_be_tested_without_a_radio(self):
        devices._reg().add_device({"id": "sim1", "transport": "sim", "label": "Sim board"})
        status, body = api.device_action("sim1", "test")
        self.assertEqual(status, 200, body)
        self.assertIn("Simulated", body["message"])

    def test_pi_own_port_cannot_be_taken_over(self):
        own = "/dev/ttyUSB-test-own"
        devices._reg().add_device({"id": "own", "transport": "serial", "port": own, "address": own})
        with mock.patch.object(devices, "_own_ports", return_value={own}):
            status, body = api.device_action("own", "connect")
            self.assertEqual(status, 409)
            self.assertIn("own radio", body["error"])
            self.assertTrue(devices.list_devices()[0]["in_use_by_pi"])


class RuntimeStartTests(unittest.TestCase):
    def test_start_rules(self):
        self.assertFalse(should_start_runtime(["manage.py", "test"], {}))
        self.assertFalse(should_start_runtime(["manage.py", "check"], {}))
        self.assertFalse(should_start_runtime(["manage.py"], {}))
        self.assertFalse(should_start_runtime(["manage.py", "runserver"], {}))             # autoreloader parent
        self.assertTrue(should_start_runtime(["manage.py", "runserver"], {"RUN_MAIN": "true"}))
        self.assertTrue(should_start_runtime(["manage.py", "runserver", "--noreload"], {}))
        self.assertTrue(should_start_runtime(["/venv/bin/gunicorn"], {}))
        self.assertFalse(should_start_runtime(["/venv/bin/gunicorn"], {"NODE_AUTOSTART": "0"}))
        self.assertFalse(should_start_runtime(["manage.py", "runserver", "--noreload"], {"NODE_AUTOSTART": "0"}))


class EnglishOnlyTests(unittest.TestCase):
    DIACRITICS = re.compile("[ăâîșşțţĂÂÎȘŞȚŢ]")
    WORDS = re.compile(r"\b(vehicul|vehicule|avertizare|alerta|sonora|vizuala|dispozitiv|dispozitive|pornit|oprit|"
                       r"apasa|distanta|viteza|trimite|conectat|simulare|panou|testare|ecran|buton|salveaza|"
                       r"deconectat|conectare|incarcare|eroare|pornire|oprire)\b", re.I)

    def files(self):
        for sub in ("web/templates", "web/static"):
            for folder, _, names in os.walk(os.path.join(ROOT, sub)):
                for name in names:
                    if name.endswith((".html", ".js", ".css")):
                        yield os.path.join(folder, name)

    def test_templates_and_static_files_exist(self):
        names = [os.path.basename(p) for p in self.files()]
        for expected in ("base.html", "dashboard.html", "devices.html", "simulation.html", "tests.html",
                         "app.css", "app.js"):
            self.assertIn(expected, names)

    def test_no_romanian_words_or_diacritics(self):
        problems = []
        for path in self.files():
            with open(path, encoding="utf-8") as handle:
                for number, line in enumerate(handle, 1):
                    if self.DIACRITICS.search(line) or self.WORDS.search(line):
                        problems.append("%s:%d" % (os.path.relpath(path, ROOT), number))
        self.assertEqual(problems, [])

    def test_the_checker_catches_romanian(self):
        self.assertTrue(self.WORDS.search("Trimite alerta"))
        self.assertTrue(self.DIACRITICS.search("pornește"))
        self.assertFalse(self.WORDS.search("Send test message"))

    def test_base_template_has_the_simulation_badge(self):
        with open(os.path.join(ROOT, "web/templates/web/base.html"), encoding="utf-8") as handle:
            self.assertIn("SIMULATION", handle.read())


@unittest.skipUnless(HAVE_DJANGO, "needs Django (run with manage.py test)")
class DjangoViewTests(SimpleTestCase):
    def setUp(self):
        saved = {k: os.environ.get(k) for k in list(_ENV)}

        def restore():
            reset_world()
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

        self.addCleanup(restore)
        os.environ.update(_ENV)
        reset_world()

    def test_pages_return_200_in_english(self):
        for name in ("index", "devices", "simulation", "tests"):
            response = self.client.get(reverse("web:" + name))
            self.assertEqual(response.status_code, 200, name)
            self.assertContains(response, "Proximity and Impact Warning")
            self.assertContains(response, "SIMULATION")
            self.assertContains(response, "csrf-token")

    def test_state_json_schema(self):
        response = self.client.get(reverse("api_state"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.json()), STATE_KEYS)

    def test_post_only_endpoints_refuse_get(self):
        for name, kwargs in (("api_pulse", {"output": "buzzer"}), ("api_outputs_off", {}),
                             ("api_distance_read", {}), ("api_mesh_test", {}), ("api_sim_distance", {}),
                             ("api_sim_vibration", {}), ("api_sim_remote_alert", {}), ("api_tests_run", {}),
                             ("api_devices_scan", {}), ("api_devices_add", {}),
                             ("api_device_action", {"device_id": "x", "action": "connect"})):
            self.assertEqual(self.client.get(reverse(name, kwargs=kwargs)).status_code, 405, name)

    def test_get_only_endpoints_refuse_post(self):
        for name in ("api_state", "api_devices", "api_tests_list"):
            self.assertEqual(self.client.post(reverse(name)).status_code, 405, name)

    def test_csrf_is_enforced_on_actuating_endpoints(self):
        from django.test import Client
        client = Client(enforce_csrf_checks=True)
        for name, kwargs in (("api_pulse", {"output": "buzzer"}), ("api_outputs_off", {}),
                             ("api_sim_distance", {}), ("api_sim_vibration", {}),
                             ("api_sim_remote_alert", {}), ("api_devices_scan", {}),
                             ("api_device_action", {"device_id": "x", "action": "remove"})):
            response = client.post(reverse(name, kwargs=kwargs), data="{}", content_type="application/json")
            self.assertEqual(response.status_code, 403, name)

    def test_json_body_is_parsed_and_validated(self):
        node.NODE_vStart()
        ok = self.client.post(reverse("api_sim_distance"), data={"distance_cm": 60},
                              content_type="application/json")
        self.assertEqual(ok.status_code, 200)
        bad = self.client.post(reverse("api_sim_distance"), data="not json", content_type="application/json")
        self.assertEqual(bad.status_code, 400)
        pulse = self.client.post(reverse("api_pulse", kwargs={"output": "buzzer"}),
                                 data={"duration_ms": 5000}, content_type="application/json")
        self.assertEqual(pulse.status_code, 400)

    def test_pulse_auto_off_through_the_view(self):
        node.NODE_vStart()
        response = self.client.post(reverse("api_pulse", kwargs={"output": "led"}), data={"duration_ms": 300},
                                    content_type="application/json")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(wait_until(lambda: pcs.PCS_u8GetGpio(config.GPIO_LED) == 1))
        self.assertTrue(wait_until(lambda: pcs.PCS_u8GetGpio(config.GPIO_LED) == 0, timeout=1.5))


if __name__ == "__main__":
    unittest.main()
