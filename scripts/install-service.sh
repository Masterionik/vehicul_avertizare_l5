#!/usr/bin/env bash
# Install the proximity systemd service (and, with --kiosk, the optional kiosk browser).
#
# Usage (from the project folder on the Raspberry Pi, after creating .venv and .env):
#   sudo ./scripts/install-service.sh [--kiosk]
#
# The templates contain __USER__, __APP_DIR__ and __USER_HOME__; they are substituted here.
set -euo pipefail

KIOSK=0
for arg in "$@"; do
  case "$arg" in
    --kiosk) KIOSK=1 ;;
    *) echo "Unknown argument: $arg (supported: --kiosk)" >&2; exit 2 ;;
  esac
done

if [[ $EUID -ne 0 ]]; then
  echo "Run with sudo: sudo $0 $*" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
RUN_USER="${SUDO_USER:-}"
if [[ -z "$RUN_USER" || "$RUN_USER" == "root" ]]; then
  echo "Run it with sudo from a normal user account, not as root: the service must not run as root." >&2
  exit 1
fi
USER_HOME="$(getent passwd "$RUN_USER" | cut -d: -f6)"

if [[ ! -x "${APP_DIR}/.venv/bin/python" ]]; then
  echo "Missing ${APP_DIR}/.venv. Create it first (see README, 'Raspberry Pi')." >&2
  exit 1
fi
if [[ ! -f "${APP_DIR}/.env" ]]; then
  echo "Missing ${APP_DIR}/.env. Run: cp .env.example .env and edit it (README, 'Configuration')." >&2
  exit 1
fi
if grep -Eq '^DJANGO_SECRET_KEY=(change-me)?$' "${APP_DIR}/.env"; then
  echo "WARNING: DJANGO_SECRET_KEY in .env is empty or still 'change-me'. Set a real value." >&2
fi

substitute() {  # substitute <template> <destination>
  sed -e "s|__USER__|${RUN_USER}|g" -e "s|__APP_DIR__|${APP_DIR}|g" \
      -e "s|__USER_HOME__|${USER_HOME}|g" "$1" > "$2"
  chmod 0644 "$2"
}

substitute "${SCRIPT_DIR}/proximity.service" /etc/systemd/system/proximity.service
UNITS=(proximity.service)

if [[ $KIOSK -eq 1 ]]; then
  substitute "${SCRIPT_DIR}/kiosk.service" /etc/systemd/system/proximity-kiosk.service
  UNITS+=(proximity-kiosk.service)
fi

systemctl daemon-reload
systemctl enable --now "${UNITS[@]}"
systemctl --no-pager status "${UNITS[@]}" || true
echo
echo "Installed: ${UNITS[*]}"
echo "Logs:      journalctl -u proximity -f"
