# Proximity and Impact Warning System

A vehicle node that measures the distance to an obstacle, detects impacts, warns the driver with a buzzer and an
LED, and shares warnings with nearby nodes over Zigbee (short range) and LoRa Meshtastic (long range). A dashboard
runs on the same Raspberry Pi.

University project for "Proiectarea sistemelor incorporate" (labs L1-L5). **Warning only: the system never controls
brakes or engine.**

## Features

- Ultrasonic distance (HC-SR04) turned into an alert level 0-3 (clear / caution / warning / danger) with debounce and
  hysteresis.
- Impact detection from digital vibration sensors (edge counting).
- Buzzer and LED patterns per level, without PWM.
- One signed 14-byte alert packet (4-byte truncated HMAC-SHA256, replay protection) used on both radios.
- Zigbee (XBee3) and LoRa Meshtastic links; each falls back to simulation when its hardware is missing, and says so
  (badge `SIMULATED`).
- AUTOSAR-style layers (`mcd`, `ioc`, `sdc`, `sched`, `rte`, `app`) with the RTE as the only door to the hardware (lab L5).
- Django dashboard: live state, devices, simulation controls, component tests.
- Everything runs and is testable on a laptop with no hardware (`MCD_SIMULATE=1`).

## Quick start: laptop (simulation)

Needs Python 3.10+.

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
MCD_SIMULATE=1 python manage.py test                      # all tests
MCD_SIMULATE=1 DJANGO_DEBUG=1 python manage.py runserver  # http://127.0.0.1:8000/
```

`DJANGO_DEBUG=1` is needed for local runs because without it Django requires a real `DJANGO_SECRET_KEY`.
Open the **Simulation** page to move the simulated distance, inject vibration edges, or make the extra vehicles V2 / V3
send alerts.

## Quick start: Raspberry Pi (real hardware)

Wire the hardware first ([docs/hardware.md](docs/hardware.md)). On Raspberry Pi OS:

```bash
sudo apt update
sudo apt install python3-lgpio python3-venv git
sudo usermod -aG dialout,gpio "$USER"        # log out and in again afterwards

cd proximity-warning                          # the project folder
python3 -m venv --system-site-packages .venv  # system site packages: lgpio comes from apt
. .venv/bin/activate
pip install -r requirements-pi.txt

cp .env.example .env
nano .env                                     # set DJANGO_SECRET_KEY, MESH_HMAC_KEY, DJANGO_ALLOWED_HOSTS
```

Test run (MCD_SIMULATE=0 is the real hardware):

```bash
set -a; . ./.env; set +a
MCD_SIMULATE=0 python manage.py runserver 0.0.0.0:8000 --noreload
```

Autostart with systemd (uses `.venv` and loads `.env`):

```bash
sudo ./scripts/install-service.sh            # add --kiosk for the optional full-screen browser
journalctl -u proximity -f                   # logs
```

Open `http://<pi-name-or-ip>:8000/` from a phone or laptop on the same network (the name or IP must be listed in
`DJANGO_ALLOWED_HOSTS`).

## Configuration

Set in `.env` (see `.env.example`). The systemd service reads it automatically; on a laptop use
`set -a; . ./.env; set +a` or put the variables in front of the command.

| Variable | Default | Meaning |
| --- | --- | --- |
| `MCD_SIMULATE` | auto (hardware present?) | `1` forces simulation, `0` forces real GPIO and radios |
| `DJANGO_DEBUG` | `0` | `1` for development only |
| `DJANGO_SECRET_KEY` | none | Required when `DJANGO_DEBUG=0` |
| `DJANGO_ALLOWED_HOSTS` | `localhost,127.0.0.1,[::1]` | Comma separated host names / IPs |
| `DJANGO_TIME_ZONE` | `UTC` | Time zone of the dashboard |
| `ZIGBEE_PORT` | auto (`/dev/serial/by-id`) | XBee serial port; required if the detection is ambiguous. Old name `MCD_ZIGBEE_PORT` still works |
| `MESHTASTIC_PORT` | auto (`/dev/serial/by-id`) | Heltec V3 serial port. Old name `MCD_MESHTASTIC_PORT` still works |
| `MCD_VIB2` | `1` | `0` if only one vibration sensor is fitted |
| `MCD_GPIOCHIP` | auto | GPIO chip number or name |
| `MESH_HMAC_KEY` | dev key (logs a warning) | Shared signing key, identical on all nodes |
| `MESH_NODE_ID` | derived from host name | Fixed 32-bit node id |
| `SIM_V2_PORT`, `SIM_V2_HOST`, `SIM_V3_PORT`, `SIM_V3_HOST` | empty | Serial port or TCP host of the extra boards used as remote vehicles V2 / V3; empty = simulated |
| `SIM_V2_NODE_ID`, `SIM_V3_NODE_ID` | derived from name | Node ids of the remote vehicles |
| `NODE_AUTOSTART` | `1` | `0` stops the web app from starting the runtime thread |

## Project structure

```text
config/    Django project package (settings, urls, wsgi)
mcd/       drivers: pcs (GPIO), ultrasonic, usb, tim, config
ioc/       logical channels -> drivers (buzzer, LED, distance, vibration)
sdc/       packets, transports, Meshtastic and Zigbee links, device registry
sched/     100 ms / 1 s / 60 s ticks
rte/       the application's only interface to the BSW + signals (lab L5), test.py = unit test
app/       logic: prx (proximity), imp (impact), alr (alerts), rpt (report), node (runtime)
sim/       scripted remote vehicles V2 / V3, device manager
web/       dashboard, API, templates, static files
tests/     tests of app, packets, layers, web
scripts/   systemd service installer, optional kiosk
docs/      architecture, hardware, rte_api, testing
```

Runtime files (`data/`, `logs/`, reports) are created on demand and ignored by git.

## Tests

```bash
MCD_SIMULATE=1 python -m mcd.test        # drivers
MCD_SIMULATE=1 python -m rte.test        # RTE unit test (lab L5)
MCD_SIMULATE=1 python manage.py test     # everything
```

Hardware tests run only with `MCD_HW_TESTS=1` on the Pi. The full plan is in [docs/testing.md](docs/testing.md).

## Troubleshooting

| Symptom | Cause / fix |
| --- | --- |
| `ImproperlyConfigured: DJANGO_SECRET_KEY is not set` | Set it in `.env`, or use `DJANGO_DEBUG=1` on a laptop |
| `DisallowedHost` / HTTP 400 in the browser | Add the host name or IP you typed to `DJANGO_ALLOWED_HOSTS` |
| `Django is not installed` | Activate the venv: `. .venv/bin/activate` |
| Dashboard shows a `SIMULATION` badge on the Pi | `MCD_SIMULATE` is `1`, `lgpio` is missing (venv without `--system-site-packages`), or `/dev/gpiochip*` is not accessible: check the `gpio` group |
| Zigbee shows `SIMULATED` | No XBee found. Check `ls -l /dev/serial/by-id/`, the `dialout` group, or set `ZIGBEE_PORT` |
| Port detection says "ambiguous" | Two matching adapters are plugged in: set `ZIGBEE_PORT` and `MESHTASTIC_PORT` |
| Distance always invalid on the Pi | ECHO divider or wiring wrong, sensor not on 5 V, or object closer than 2 cm / farther than 4 m |
| Constant false impacts | Vibration sensor too sensitive: turn its potentiometer, or mount it away from the buzzer |
| `Permission denied: /dev/ttyUSB0` | `sudo usermod -aG dialout $USER`, then log in again |
| Warning "development HMAC key" in the log | Set `MESH_HMAC_KEY` (same value on every node) |
| Packets from another node are dropped | Different `MESH_HMAC_KEY`, or the sender repeated an old sequence number (replay guard) |
| `pip install` fails for `lgpio` on the Pi | Do not install it with pip: `sudo apt install python3-lgpio` and use `--system-site-packages` |
| Static files missing | They are served by WhiteNoise from `web/static`; with a custom setup run `python manage.py collectstatic` |
| Service does not start | `journalctl -u proximity -e`; usually a missing `.env` value or a wrong venv path |

## More documentation

- [docs/architecture.md](docs/architecture.md): layers, data flow, packet format, threading, Zigbee vs LoRa.
- [docs/hardware.md](docs/hardware.md): parts, wiring, pins, radio setup.
- [docs/rte_api.md](docs/rte_api.md): every RTE function in the lab table format.
- [docs/testing.md](docs/testing.md): test plan and acceptance checklist.
- [AGENTS.md](AGENTS.md): project rules.
