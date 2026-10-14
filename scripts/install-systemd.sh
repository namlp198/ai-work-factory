#!/usr/bin/env bash
# Install AI Work Factory as systemd services on Linux (Ubuntu/Debian/Jetson).
#
#   sudo scripts/install-systemd.sh               install, enable at boot, start now
#   sudo scripts/install-systemd.sh --no-start    install and enable, start later
#   sudo scripts/install-systemd.sh --uninstall   stop, disable and remove the units
#
# Run scripts/bootstrap.sh first. The service runs `awf start` as the user who owns this checkout,
# so AWF_ROLE in .env decides what it does (control-plane, worker or all). After changing .env or
# pulling new code (and re-running bootstrap): sudo systemctl restart awf
set -euo pipefail

action="install"; start_now=1
for arg in "$@"; do
  case "$arg" in
    --uninstall) action="uninstall" ;;
    --no-start) start_now=0 ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done

[[ $EUID -eq 0 ]] || { echo "run with sudo: sudo $0 $*" >&2; exit 1; }
command -v systemctl >/dev/null && [[ -d /run/systemd/system ]] || { echo "systemd is not running on this machine" >&2; exit 1; }

repo="$(cd "$(dirname "$0")/.." && pwd)"
units=(awf.service awf-backup.service awf-backup.timer)
target=/etc/systemd/system

if [[ "$action" == "uninstall" ]]; then
  systemctl disable --now awf-backup.timer awf.service 2>/dev/null || true
  for unit in "${units[@]}"; do rm -f "$target/$unit"; done
  systemctl daemon-reload
  echo "AI Work Factory services removed (data in $repo/var is untouched)."
  exit 0
fi

# The service runs as the owner of the checkout, never as root.
user="$(stat -c %U "$repo")"
group="$(stat -c %G "$repo")"
[[ "$user" != "root" ]] || { echo "the checkout is owned by root; clone it as a normal user" >&2; exit 1; }
[[ -x "$repo/.venv/bin/awf" ]] || { echo "missing $repo/.venv/bin/awf: run scripts/bootstrap.sh first" >&2; exit 1; }
[[ -f "$repo/.env" ]] || { echo "missing $repo/.env: run scripts/bootstrap.sh first" >&2; exit 1; }

env_value() { grep -E "^$1=" "$repo/.env" | tail -1 | cut -d= -f2- || true; }
role="$(env_value AWF_ROLE)"; role="${role:-all}"
database_url="$(env_value DATABASE_URL)"; database_url="${database_url:-sqlite:///./var/awf.db}"

render() {
  sed -e "s|@REPO@|$repo|g" -e "s|@USER@|$user|g" -e "s|@GROUP@|$group|g" \
    "$repo/infrastructure/deploy/systemd/$1" > "$target/$1"
  chmod 644 "$target/$1"
}

render awf.service
backup=0
if [[ "$role" != "worker" && "$database_url" == sqlite* ]]; then
  render awf-backup.service
  render awf-backup.timer
  backup=1
else
  rm -f "$target/awf-backup.service" "$target/awf-backup.timer"
fi
systemctl daemon-reload

if (( start_now )); then
  systemctl enable --now awf.service
  (( backup )) && systemctl enable --now awf-backup.timer
else
  systemctl enable awf.service
  (( backup )) && systemctl enable awf-backup.timer
fi

echo
echo "Installed awf.service (role=$role, user=$user, repo=$repo)."
(( backup )) && echo "Installed awf-backup.timer: nightly backup to $repo/var/backups (newest 14 kept)."
[[ "$role" != "worker" && "$database_url" != sqlite* ]] && echo "PostgreSQL detected: schedule pg_dump yourself."
cat <<EOF

Useful commands:
  systemctl status awf              # is it running?
  journalctl -u awf -f              # live logs (JSON lines)
  sudo systemctl restart awf        # after editing .env or after git pull + bootstrap
  sudo systemctl stop awf           # stop (also: disable to keep it off after reboot)
  systemctl list-timers awf-backup  # next backup
EOF
if [[ "$role" != "worker" ]]; then
  port="$(env_value AWF_API_PORT)"; port="${port:-8000}"
  host="$(env_value AWF_API_HOST)"
  if [[ "$host" != "127.0.0.1" && "$host" != "localhost" ]] && command -v ufw >/dev/null && ufw status 2>/dev/null | grep -q "Status: active"; then
    echo
    echo "ufw is active: allow workers on your LAN to reach the API, e.g."
    echo "  sudo ufw allow from 192.168.0.0/16 to any port $port proto tcp"
  fi
fi
