# Project rules: Proximity and Impact Warning System

## 1. What the system is
University embedded-systems project (course "Proiectarea sistemelor incorporate", labs L1-L5).
A vehicle node measures the distance to an obstacle or vehicle with an ultrasonic sensor, detects
impacts or hard shocks with vibration sensors, warns the driver with a buzzer and an LED, and shares
warnings with nearby nodes over Zigbee (short range) and LoRa Meshtastic (long range / control centre).
A Django dashboard runs on the same Raspberry Pi. **Warning only: the system never controls brakes or engine.**

## 2. Hardware (this is ALL the hardware that exists)
- Raspberry Pi 4 (development, hostname `Pi4Master`, user `pi4`) and Raspberry Pi 5 (target).
- Ultrasonic sensor, HC-SR04 compatible: TRIG = GPIO23, ECHO = GPIO24. ECHO is 5 V, so it needs a
  voltage divider (1 kOhm in series, then 2 kOhm to GND, so the Pi pin sees about 3.3 V). Sensor VCC = 5 V.
- Active buzzer on GPIO17 through an NPN transistor (1 kOhm base resistor). On/off only, no PWM.
- LED on GPIO27 with a 330 Ohm resistor to GND.
- Vibration sensor(s), digital output type (for example SW-420 module): VIB1 = GPIO22, optional VIB2 = GPIO25.
  They provide impact / hard-shock detection. Count edges (pulses), do not rely on polling a single level.
- Zigbee: XBee3 module on a USB adapter, 9600 baud, transparent mode.
- Three boards with stock Meshtastic firmware (no custom firmware): Heltec WiFi LoRa 32 V3, Heltec e-ink, Seeed XIAO.
  - The Heltec V3 is the Pi's own radio ("modem"), connected by USB serial.
  - The Heltec e-ink and the XIAO act as simulated remote vehicles (V2, V3) that send alerts to the Pi over LoRa.
    They connect to the Pi by USB serial or WiFi/TCP (port 4403) so the app can drive and test them.
- NOT present: GPS, MPU6050 / I2C accelerometer. Remove all code, docs and UI for them. There is no
  position or route logic anymore.
- Serial port names are NOT stable: the XBee adapter and the Heltec V3 can both appear as `/dev/ttyUSB0`
  (it depends on plug order), and native-USB boards appear as `/dev/ttyACM*`. Always identify devices via
  `/dev/serial/by-id/*` (vendor/product strings), with env overrides `ZIGBEE_PORT` and `MESHTASTIC_PORT`.
- Meshtastic is not LoRaWAN. Always write "LoRa Meshtastic".
- If the XBee is not connected, Zigbee runs in simulation and the docs and UI must say so honestly (badge "SIMULATED").

## 3. Language
EVERYTHING is English only: code, identifiers, comments, docstrings, commit messages, log messages,
web UI text, API field names, error messages, documentation. No Romanian text anywhere.

## 4. Architecture (AUTOSAR-style layers from lab L3, keep these layer names)
```
web (Django views, templates)  ->  app (logic)  ->  rte  ->  ioc / sdc / sched  ->  mcd
                               ->  sim (remote-vehicle simulation + device manager, uses sdc and rte)
```
- `mcd`: low-level drivers (BSW): `pcs` (GPIO), `ultrasonic`, `usb` (serial), `tim`, `config`.
- `ioc`: maps logical channels to MCD (buzzer, LED, distance sensor, vibration inputs).
- `sdc`: communication: transports (sim, serial, tcp), `meshtastic_link`, `zigbee_link`, `packets`, `registry`.
- `sched`: timing helpers on top of `tim`.
- `rte`: the ONLY interface the application uses to reach the BSW and to exchange signals between
  application modules (lab L5).
- `app`: pure logic: `prx` (proximity level), `imp` (impact detection), `alr` (alert manager), `rpt` (report),
  `node` (runtime step).
- `sim`: drives the extra Meshtastic boards as simulated remote vehicles and manages devices (list, connect,
  test message). May import `sdc` and `rte`.
- `web`: dashboard + API. Imports only `rte`, `app`, `sim`.
Dependency rules (enforced by a test that parses imports with `ast`):
- `app` imports only `rte` (and the standard library).
- `web` imports `rte`, `app`, `sim` only.
- `sim` imports `rte`, `sdc` only.
- `rte` imports `ioc`, `sdc`, `sched` only.
- `ioc`, `sdc`, `sched` import `mcd` (sdc may also use third-party libs such as pyserial, meshtastic).
- `mcd` imports nothing from the other project layers and no Django.
- Nothing imports upward.

## 5. Target folder structure
```
proximity-warning/
├─ README.md                 # single entry point: overview, quick start laptop + Pi, troubleshooting
├─ AGENTS.md
├─ manage.py
├─ requirements.txt          # Django, pyserial, meshtastic, whitenoise (works on a laptop)
├─ requirements-pi.txt       # -r requirements.txt (lgpio comes from apt on the Pi)
├─ .env.example
├─ .gitignore
├─ config/                   # Django project package (renamed from "vehicul")
├─ mcd/    config.py pcs.py ultrasonic.py usb.py tim.py test.py
├─ ioc/    __init__.py
├─ sdc/    packets.py transport.py transport_sim.py transport_serial.py transport_tcp.py
│          meshtastic_link.py zigbee_link.py registry.py
├─ sched/  __init__.py
├─ rte/    __init__.py test.py            # test.py is the L5 "unit test" file
├─ app/    prx.py imp.py alr.py rpt.py node.py
├─ sim/    vehicles.py devices.py
├─ web/    views.py urls.py apps.py templates/web/*.html static/web/{css,js}/*
├─ scripts/  install-service.sh  proximity.service  (+ optional kiosk.sh / kiosk.service)
├─ tests/  test_app.py test_packets.py test_layers.py test_web.py
└─ docs/   architecture.md  hardware.md  rte_api.md  testing.md
```
Generated runtime files (`data/`, `logs/`, reports) are git-ignored and created at runtime.

## 6. L5 requirements (from lab "L5 RTE development")
The RTE links the application layer to the BSW and the application modules to each other. It makes the
application independent of the hardware.
1. Implement the RTE functions needed for (a) communication between application modules and (b) access to the BSW.
2. Create a unit-test file in which ALL implemented RTE functions are tested (the lab calls it `unit.c`; in this
   Python project it is `rte/test.py`, run with `python -m rte.test`, also discovered by `python manage.py test`).
Lab conventions to follow:
- Naming: `RTE_vSetX(u8Value)` for setters (void), `RTE_u8GetX()` / `RTE_u16GetX()` for getters.
  Prefix `v` = void, `u8` = unsigned 8-bit, `u16` = unsigned 16-bit, `b` = bool, `t` = tuple/dict.
- Binary parameters and return values are in the interval 0-1. Invalid parameters are IGNORED (the output does not
  change). Accept only real ints 0 or 1 for binary values (reject bool, float, None, str, 2, -1).
- Every function is documented with a table: Name | Description | Parameters (name, type, description, interval) |
  Return value (type, description, interval) | Reentrant (Yes/No). The tables live in `docs/rte_api.md`;
  docstrings repeat the facts in short form.

### RTE function set to implement (all in `rte/__init__.py`)
| Group | Function | Behaviour |
| --- | --- | --- |
| Lifecycle | `RTE_vInit()`, `RTE_vDeinit()` | Init layers; deinit turns buzzer and LED off, closes radio links, resets signals; calling init twice is safe |
| BSW output | `RTE_vSetBuzzer(u8Value)` | 0/1 -> IOC buzzer channel -> GPIO17 |
| BSW output | `RTE_vSetLed(u8Value)` | 0/1 -> IOC LED channel -> GPIO27 |
| BSW input | `RTE_u16GetDistanceCm()` | Distance in cm, valid 2-400; returns `RTE_DISTANCE_INVALID = 0xFFFF` on no echo / out of range |
| BSW input | `RTE_u8GetDistanceSensorStatus()` | 1 if the last measurement was valid, else 0 |
| BSW input | `RTE_u16GetVibrationEdges()` | Number of vibration edges since the previous call (consumes the count) |
| BSW input | `RTE_u8GetVibrationState()` | Current raw level of the vibration input, 0/1 |
| Timing | `RTE_u8GetTick100ms()`, `RTE_u8GetTick1s()`, `RTE_u8GetTick60s()` | Return 1 once per elapsed period (consumes the event), else 0 |
| Meshtastic | `RTE_u8GetMeshStatus()` | 1 if the modem link is connected |
| Meshtastic | `RTE_u16SendMeshAlert(u8Kind, u8Level, u16DistanceCm)` | Builds a signed alert packet and sends it; returns bytes sent, 0 on invalid args or no link |
| Meshtastic | `RTE_u16SendMeshText(text)` | Sends a short text (device test); returns bytes sent or 0 |
| Meshtastic | `RTE_tGetMeshAlert()` | Oldest valid received alert as dict `{node, kind, level, distance_cm, seq}` or `None`; bad signature or replayed packets are dropped |
| Zigbee | `RTE_u8GetZigbeeStatus()`, `RTE_u16SendZigbeeAlert(u8Kind, u8Level, u16DistanceCm)`, `RTE_tGetZigbeeAlert()` | Same contract as the Meshtastic functions, over the Zigbee link (simulated if no XBee) |
| Signals (virtual functional bus between app modules) | `RTE_vWriteDistanceCm(u16)` / `RTE_u16ReadDistanceCm()` | Latest filtered distance (written by PRX, read by ALR/RPT/web) |
| Signals | `RTE_vWriteAlertLevel(u8)` / `RTE_u8ReadAlertLevel()` | Local alert level 0-3 |
| Signals | `RTE_vWriteRemoteAlertLevel(u8)` / `RTE_u8ReadRemoteAlertLevel()` | Level received from another node |
| Signals | `RTE_vWriteImpact(u8)` / `RTE_u8ReadImpact()` | Impact flag 0/1 (written by IMP, read by ALR/web) |
Alert kind values: 1 = proximity, 2 = impact. Alert level: 0 clear, 1 caution, 2 warning, 3 danger.
Signal storage is protected by a lock. Hardware access is serialized by ONE runtime thread; web views never touch
hardware directly (they read signals or enqueue a request). Remove the old `vSetAlertaSonora` / `vSetAlertaVizuala`.

## 7. Application behaviour
- `app/prx.py`: distance -> alert level with hysteresis and debounce (a level changes only after 3 consecutive
  samples). Defaults: level 0 clear (>150 cm), 1 caution (<=150), 2 warning (<=80), 3 danger (<=30). Thresholds
  are constants in one place. Invalid readings keep the previous level for up to 1 s, then fall back to level 0
  and raise a "sensor fault" flag.
- `app/imp.py`: impact detection from vibration edges: an impact is declared when at least N edges (default 3)
  occur within a 300 ms window; then a 3 s lockout prevents repeats. Constants in one place.
- `app/alr.py`: alert manager. Buzzer and LED patterns without PWM: level 1 = 200 ms on / 800 ms off,
  level 2 = 200 / 300, level 3 = continuous; an impact = 2 s continuous buzzer + rapid LED flashing.
  A remote alert gives two short beeps, keeps the LED on for 5 s and shows on the dashboard.
  Sends an alert over Zigbee at most once per 1 s and over Meshtastic at most once per 5 s while the level stays
  >= 2 or an impact occurred (LoRa airtime is scarce). Buzzer and LED are ALWAYS forced off on shutdown or when
  the runtime thread stops.
- `app/rpt.py`: 60 s status report (counts, max level, impacts, sensor faults, TX/RX counters per radio).
- `app/node.py`: `NODE_vStep()` called every 100 ms by ONE background thread started from `web/apps.py`.
- `sim/vehicles.py`: uses the extra Meshtastic boards (Heltec e-ink = V2, XIAO = V3) to send scripted alert
  packets to the Pi (for example "V2 warning at 40 cm", "V3 impact"), so the whole chain can be demonstrated
  with real LoRa hardware and no extra vehicles. `sim/devices.py`: list, connect, test message, device logs.
- Simulation: with `MCD_SIMULATE=1` (or no hardware present) the sensor returns a settable simulated distance,
  vibration edges can be injected, the buzzer and LED are memory flags, and both radios use `SimulatedTransport`.
  Everything must run and be testable on a laptop with no hardware.

## 8. Web UI (English, dark theme, black / green / purple palette)
- Dashboard: big live distance, alert level indicator (0-3), buzzer and LED state, impact indicator, Meshtastic and
  Zigbee status (real or SIMULATED badge), last remote alert, event log. Polls `/api/state/` every 500 ms.
- Devices: Meshtastic nodes: list, connect by USB or TCP, show info, send a test message, RX log.
- Simulation: buttons to make the extra boards send a scripted remote alert; sliders to set the simulated distance
  and to inject vibration edges when the hardware is simulated.
- Tests: component tests (buzzer + LED pulse, single ultrasonic read, vibration count, Meshtastic and Zigbee
  loopback/send, RTE self-test) with PASS/FAIL badges.
- Any endpoint that actuates hardware: POST only, CSRF-protected, buzzer/LED auto-off after at most 2 s, refused
  while a real alert is active. Destructive device actions need explicit `confirm=true` and are logged.
- No dummy/demo data in production code paths. A visible "SIMULATION" badge whenever a part is simulated.

## 9. Known defects in the current code (fix them, with tests)
1. `tests/test_phase1.py` has assertions like `assertTrue(True)`: tests that cannot fail. Replace them with real
   assertions (for example: after `RTE_vSetBuzzer(1)`, `PCS_u8GetGpio(17) == 1`).
2. `sdc/packets.py: validate_replay` is wrong: it returns True only when `seq == last_seq` and False for a newer
   packet. Correct rule: accept only if `seq` is newer than `last_seq` within a window, with 8-bit wrap-around;
   reject equal or older. Fix the test that asserted the wrong behaviour.
3. The HMAC key is hard-coded in `packets.py`: read it from env `MESH_HMAC_KEY` (a dev default may exist but must
   log a warning). Keep the 4-byte truncated HMAC. Use the same packet format on both radios.
4. `mcd/` contains Django views/templates/tests: move all of that to `web/`.
5. `vehicul/settings.py`: remove the hard-coded LAN IP in `ALLOWED_HOSTS`; DEBUG must default to off; no insecure
   fallback SECRET_KEY outside DEBUG; read config from env. Rename the package to `config`.
6. Static files do not load with `DEBUG=0` under `runserver`: use WhiteNoise.
7. `scripts/vehicul-alert.service` runs `/usr/bin/python3` (no venv; system pip is blocked on Pi OS) and never
   loads `.env`: use the venv Python and `EnvironmentFile=`. The kiosk is optional, not default.
8. `data/devices.json` contains junk `/dev/ttyS*` entries (runtime data committed to git): git-ignore it and make
   the registry list only plausible USB serial devices (`/dev/ttyUSB*`, `/dev/ttyACM*`, `/dev/serial/by-id/*`).
9. Dependencies: `meshtastic>=2.7.5` (3.x does not exist), plain `lgpio` (installed from apt on the Pi:
   `python3-lgpio`; venv created with `--system-site-packages`), `pyserial`, `Django>=5.2,<6`, `whitenoise`.

## 10. Working rules for the agent
- Work in the current branch. Small, reviewable commits: one per phase.
- Do NOT read or print whole large files or the whole repo when not needed; open only the files named in the phase.
- Do not rename existing MCD function names (`PCS_vSetOutputGpio`, `USB_u16Write`, `TIM_bHasElapsed`, ...);
  adding functions or a TIM channel is allowed.
- Keep driver behaviour: invalid parameters are ignored or raise exactly what current tests expect; never weaken
  a test, only replace vacuous ones with real ones.
- If a test fails twice, stop and report the failing output instead of looping.
- Final reply of every phase: at most 15 lines: what changed, test command + result, open issues.
  Do not paste file contents back.
- Test commands: `MCD_SIMULATE=1 python -m mcd.test`, `MCD_SIMULATE=1 python -m rte.test`,
  `MCD_SIMULATE=1 python manage.py test`.
