# Test plan

## 1. Automated tests (no hardware)

Run all three from the project folder with the virtual environment active.

| Command | What it covers |
| --- | --- |
| `MCD_SIMULATE=1 python -m mcd.test` | Drivers: GPIO, ultrasonic (valid range, timeout, thread safety), edge counter and debounce, USB, timers |
| `MCD_SIMULATE=1 python -m rte.test` | RTE unit test (lab L5): every RTE function, invalid arguments ignored, loopback of both radios, signals under 8 threads, lifecycle |
| `MCD_SIMULATE=1 python manage.py test` | Everything above plus `ioc`, `sched`, `tests/test_packets.py`, `tests/test_app.py`, `tests/test_layers.py`, `tests/test_web.py` |

Also run `python manage.py check` (needs `DJANGO_DEBUG=1` or a `DJANGO_SECRET_KEY`).

Important tests:

- Layer rules: `tests/test_layers.py` fails when an import breaks the dependency rules.
- Packet: round trip, tampered byte, wrong key, replay, wrap-around, framing resync (`tests/test_packets.py`).
- Application: thresholds at exactly 150 / 80 / 30 cm, debounce, impact window and lockout, TX rate limits, shutdown forces
  buzzer and LED off (`tests/test_app.py`).
- API documentation: the number of functions in `docs/rte_api.md` equals the number of public RTE functions in code.
- Language: templates and static files must not contain Romanian words or diacritics.

## 2. Hardware smoke test on the Raspberry Pi

Wire the hardware, then:

```bash
MCD_SIMULATE=0 MCD_HW_TESTS=1 python -m mcd.test
```

Then start the dashboard (`MCD_SIMULATE=0`) and open the **Tests** page. Run each component test and confirm the
physical effect:

| Test | Expected |
| --- | --- |
| Buzzer pulse | audible beep, off after at most 2 s |
| LED pulse | LED lights, off after at most 2 s |
| Ultrasonic read | a distance in cm that follows a hand moved in front of the sensor; invalid when nothing is in range |
| Vibration count | knocking on the sensor increases the count |
| Meshtastic send | the other boards receive the test text |
| Zigbee send | a second XBee receives the packet (simulated badge if no XBee) |
| RTE self-test | PASS |

## 3. Two-radio test with the extra boards

1. Flash and configure the boards as described in [hardware.md](hardware.md); set `SIM_V2_PORT` and `SIM_V3_PORT` (or `*_HOST`)
   in `.env` and restart the service.
2. On the **Simulation** page press "V2 warning at 40 cm" and "V3 impact".
3. Expect: the packet arrives over LoRa, the dashboard shows the last remote alert with the sender, the buzzer gives two
   short beeps and the LED stays on for 5 s. The simulation result must say `simulated: false`.
4. Repeat with a second XBee as the sender for Zigbee.
5. Change `MESH_HMAC_KEY` on one board's sender (or edit a captured packet): the Pi must drop the packet and count it as a bad signature.
6. Send the same packet twice: the second one must be dropped as a replay.

## 4. Acceptance checklist

Fill in the table during the final demonstration.

| # | Criterion | How to check | Result (PASS / FAIL) | Notes |
| --- | --- | --- | --- | --- |
| 1 | Alert within 500 ms of the threshold crossing | Move an object from 160 cm to 70 cm; record the time to the buzzer; the 3-sample debounce takes about 300 ms | | |
| 2 | No false alarm at rest | Leave the node for 10 minutes with nothing in front and no knocks; level stays 0, no impact | | |
| 3 | Impact detected | Knock the vibration sensor 3 times quickly; impact flag, 2 s buzzer, LED flashing | | |
| 4 | Replayed packet rejected | Re-send an old packet; it is dropped and counted | | |
| 5 | Tampered packet rejected | Change one byte or use another key; it is dropped and counted | | |
| 6 | Buzzer and LED off after shutdown | Trigger a level 3 alert, then `sudo systemctl stop proximity`; both outputs are off | | |
| 7 | Remote alert from LoRa Meshtastic | Section 3 step 3 | | |
| 8 | Remote alert from Zigbee | Section 3 step 4 | | |
| 9 | Dashboard honest about simulation | Unplug the XBee; Zigbee shows `SIMULATED` | | |
| 10 | Autostart after reboot | `sudo reboot`; the dashboard comes back without a login | | |

## 5. Results log

| Date | Tester | Commit | mcd tests | rte tests | manage.py test | Hardware smoke test |
| --- | --- | --- | --- | --- | --- | --- |
| | | | | | | |
