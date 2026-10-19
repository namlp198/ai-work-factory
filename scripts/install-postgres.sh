#!/usr/bin/env bash
# PostgreSQL for the control plane (Ubuntu 24.04 / Debian). Idempotent: safe to run again.
#
#   sudo scripts/install-postgres.sh                  install PostgreSQL, create role + database, point .env at it
#   sudo scripts/install-postgres.sh --import-sqlite  ... and copy the existing var/awf.db into it
#
# What it does:
#   1. apt install postgresql postgresql-client (PostgreSQL 16 on Ubuntu 24.04), enabled at boot
#   2. role "awf" with a generated password and database "awf" owned by it; listens on localhost only
#      (workers never talk to the database, only to the control-plane API)
#   3. DATABASE_URL in .env (previous .env kept as .env.bak-<time>), then `awf db upgrade`
#   4. with --import-sqlite: stops awf, copies every row of var/awf.db, starts awf again
# Run scripts/bootstrap.sh first. Then (re)install the services: sudo scripts/install-systemd.sh
set -euo pipefail

db="awf"; db_user="awf"; import_sqlite=0
for arg in "$@"; do
  case "$arg" in
    --import-sqlite) import_sqlite=1 ;;
    -h|--help) sed -n '2,14p' "$0"; exit 0 ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done

[[ $EUID -eq 0 ]] || { echo "run with sudo: sudo $0 $*" >&2; exit 1; }
command -v apt-get >/dev/null || { echo "this script supports Ubuntu/Debian (apt) only" >&2; exit 1; }

repo="$(cd "$(dirname "$0")/.." && pwd)"
user="$(stat -c %U "$repo")"
[[ "$user" != "root" ]] || { echo "the checkout is owned by root; clone it as a normal user" >&2; exit 1; }
[[ -x "$repo/.venv/bin/awf" ]] || { echo "missing $repo/.venv/bin/awf: run scripts/bootstrap.sh first" >&2; exit 1; }
[[ -f "$repo/.env" ]] || { echo "missing $repo/.env: run scripts/bootstrap.sh first" >&2; exit 1; }
as_user() { sudo -u "$user" -H -- "$@"; }
awf() { (cd "$repo" && as_user "$repo/.venv/bin/awf" "$@"); }
env_value() { grep -E "^$1=" "$repo/.env" | tail -1 | cut -d= -f2- || true; }

role="$(env_value AWF_ROLE)"
[[ "$role" != "worker" ]] || { echo "AWF_ROLE=worker: workers do not use a database, nothing to do" >&2; exit 1; }

# 1. Server -------------------------------------------------------------------------------------------
if ! command -v psql >/dev/null || ! ls /usr/lib/postgresql/*/bin/postgres >/dev/null 2>&1; then
  echo "Installing PostgreSQL..."
  DEBIAN_FRONTEND=noninteractive apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq postgresql postgresql-client
fi
systemctl enable --now postgresql >/dev/null
for _ in $(seq 1 30); do pg_isready -q -h 127.0.0.1 && break; sleep 1; done
pg_isready -q -h 127.0.0.1 || { echo "PostgreSQL did not start: journalctl -u postgresql" >&2; exit 1; }
# Statements go through stdin so the generated password never shows up in `ps`.
psql_admin() { (cd /tmp && sudo -u postgres psql -v ON_ERROR_STOP=1 -qtAX "$@"); }

# 2. Role and database --------------------------------------------------------------------------------
current_url="$(env_value DATABASE_URL)"
password=""
if [[ "$current_url" =~ ^postgresql(\+psycopg)?://${db_user}:([^@]+)@(127\.0\.0\.1|localhost)(:5432)?/${db}$ ]]; then
  password="${BASH_REMATCH[2]}"   # .env already points here: keep its password
fi
if [[ -z "$password" ]]; then
  password="$(openssl rand -hex 24 2>/dev/null || head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n')"
fi
if [[ "$(psql_admin -c "SELECT 1 FROM pg_roles WHERE rolname = '${db_user}'")" == "1" ]]; then
  psql_admin <<< "ALTER ROLE ${db_user} WITH LOGIN PASSWORD '${password}';"
else
  psql_admin <<< "CREATE ROLE ${db_user} WITH LOGIN PASSWORD '${password}';"
fi
if [[ "$(psql_admin -c "SELECT 1 FROM pg_database WHERE datname = '${db}'")" != "1" ]]; then
  psql_admin -c "CREATE DATABASE ${db} OWNER ${db_user} ENCODING 'UTF8' TEMPLATE template0"
fi
url="postgresql+psycopg://${db_user}:${password}@127.0.0.1:5432/${db}"

# 3. .env + schema ------------------------------------------------------------------------------------
if [[ "$current_url" != "$url" ]]; then
  backup="$repo/.env.bak-$(date +%Y%m%d-%H%M%S)"
  cp -p "$repo/.env" "$backup"
  if grep -qE '^DATABASE_URL=' "$repo/.env"; then
    sed -i "s|^DATABASE_URL=.*|DATABASE_URL=${url}|" "$repo/.env"
  else
    echo "DATABASE_URL=${url}" >> "$repo/.env"
  fi
  chown "$user:$(stat -c %G "$repo")" "$repo/.env"   # sed -i recreated it as root
  chmod 600 "$repo/.env"
  echo "DATABASE_URL updated in .env (previous file: $backup)"
fi

was_running=0
if systemctl is-active --quiet awf 2>/dev/null; then was_running=1; fi

if (( import_sqlite )); then
  sqlite_url="$current_url"
  [[ "$sqlite_url" == sqlite* ]] || sqlite_url="sqlite:///./var/awf.db"
  sqlite_file="${sqlite_url#sqlite:///}"
  [[ "$sqlite_file" == /* ]] || sqlite_file="$repo/${sqlite_file#./}"
  [[ -f "$sqlite_file" ]] || { echo "no SQLite database at $sqlite_file to import" >&2; exit 1; }
  (( was_running )) && { echo "Stopping awf for the import..."; systemctl stop awf; }
  # Both databases at the newest revision, then copy (one transaction: all or nothing).
  (cd "$repo" && as_user env DATABASE_URL="sqlite:///$sqlite_file" "$repo/.venv/bin/awf" db upgrade)
  awf db upgrade
  awf db import-sqlite "$sqlite_file"
  echo "The SQLite file is left untouched at $sqlite_file; delete it once you are happy with PostgreSQL."
else
  awf db upgrade
fi

(( was_running )) && { systemctl restart awf; echo "awf restarted on PostgreSQL."; }
awf doctor || true
cat <<EOF

PostgreSQL is ready: database '${db}', role '${db_user}', localhost only.
  Backups:  sudo scripts/install-systemd.sh   (nightly pg_dump to $repo/var/backups, newest 14 kept)
            or by hand: .venv/bin/awf db backup
  Restore:  sudo systemctl stop awf && .venv/bin/awf db restore var/backups/<file>.dump --yes && sudo systemctl start awf
  Shell:    sudo -u postgres psql ${db}
EOF
