#!/usr/bin/env bash
# One-time (and after every `git pull`) setup of a Linux/macOS machine (x86_64 or arm64/Jetson).
# Idempotent: safe to run again; it never overwrites an existing .env.
#
#   scripts/bootstrap.sh                                    # role all (control plane + local worker)
#   scripts/bootstrap.sh --role control-plane [--public-url https://factory.example.com]
#   scripts/bootstrap.sh --role worker --control-url http://factory-host:8000 --registration-token <token>
set -euo pipefail

role="all"; control_url=""; registration_token=""; public_url=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --role) role="$2"; shift 2 ;;
    --control-url) control_url="$2"; shift 2 ;;
    --registration-token) registration_token="$2"; shift 2 ;;
    --public-url) public_url="$2"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
case "$role" in all|control-plane|worker) ;; *) echo "invalid --role $role" >&2; exit 2 ;; esac

cd "$(dirname "$0")/.."
want="$(tr -d '[:space:]' < .python-version)"
command -v git >/dev/null || { echo "git not found on PATH" >&2; exit 1; }

find_python() {
  for name in "python$want" python3 python; do
    if command -v "$name" >/dev/null; then
      ver="$("$name" -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")')"
      [[ "$ver" == "$want" ]] && { command -v "$name"; return 0; }
    fi
  done
  echo "Python $want not found (e.g. sudo apt install python$want python$want-venv)" >&2
  return 1
}

# 1. Virtual environment on the pinned Python version. Recreated if the version differs or if it is
#    broken (e.g. left without pip by an earlier failed attempt).
if [[ -x .venv/bin/python ]]; then
  have="$(.venv/bin/python -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")' 2>/dev/null || echo broken)"
  if [[ "$have" != "$want" ]]; then
    echo "Recreating .venv ($have -> $want)"; rm -rf .venv
  elif ! .venv/bin/python -m pip --version >/dev/null 2>&1; then
    echo "Recreating .venv (it has no pip; an earlier setup was interrupted)"; rm -rf .venv
  fi
fi
if [[ ! -x .venv/bin/python ]]; then
  python="$(find_python)"
  # Debian/Ubuntu ship venv/ensurepip as a separate package.
  if ! "$python" -c 'import ensurepip, venv' >/dev/null 2>&1; then
    echo "Python $want is missing the venv module. Install it, then rerun this script:" >&2
    echo "  sudo apt install -y python$want-venv" >&2
    exit 1
  fi
  echo "Creating .venv with $python"
  if ! "$python" -m venv .venv; then
    rm -rf .venv  # never leave a half-created environment behind
    echo "Could not create .venv (on Debian/Ubuntu: sudo apt install -y python$want-venv)" >&2
    exit 1
  fi
fi

# 2. Exact dependency versions from requirements.lock, then the project itself.
.venv/bin/python -m pip install --quiet --disable-pip-version-check -r requirements.lock
.venv/bin/python -m pip install --quiet --disable-pip-version-check --no-deps -e .

# 3. Machine configuration (.env) - created once, never overwritten.
env_args=(env init --role "$role")
[[ -n "$control_url" ]] && env_args+=(--control-url "$control_url")
[[ -n "$registration_token" ]] && env_args+=(--registration-token "$registration_token")
[[ -n "$public_url" ]] && env_args+=(--public-url "$public_url")
.venv/bin/awf "${env_args[@]}"

# 4. Database schema (control plane only) and a health report.
[[ "$role" != "worker" ]] && .venv/bin/awf db upgrade
.venv/bin/awf doctor || true
echo
echo "Start this machine with:  .venv/bin/awf start"
