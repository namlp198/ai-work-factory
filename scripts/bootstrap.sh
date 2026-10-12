#!/usr/bin/env bash
# One-time (and after every `git pull`) setup of a Linux/macOS machine (x86_64 or arm64/Jetson).
# Idempotent: safe to run again; it never overwrites an existing .env.
#
#   scripts/bootstrap.sh                                    # role all (control plane + local worker)
#   scripts/bootstrap.sh --role control-plane
#   scripts/bootstrap.sh --role worker --control-url http://factory-host:8000 --registration-token <token>
set -euo pipefail

role="all"; control_url=""; registration_token=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --role) role="$2"; shift 2 ;;
    --control-url) control_url="$2"; shift 2 ;;
    --registration-token) registration_token="$2"; shift 2 ;;
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

# 1. Virtual environment on the pinned Python version (recreated if the version differs).
if [[ -x .venv/bin/python ]]; then
  have="$(.venv/bin/python -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")')"
  [[ "$have" != "$want" ]] && { echo "Recreating .venv ($have -> $want)"; rm -rf .venv; }
fi
if [[ ! -x .venv/bin/python ]]; then
  python="$(find_python)"
  echo "Creating .venv with $python"
  "$python" -m venv .venv
fi

# 2. Exact dependency versions from requirements.lock, then the project itself.
.venv/bin/python -m pip install --quiet --disable-pip-version-check -r requirements.lock
.venv/bin/python -m pip install --quiet --disable-pip-version-check --no-deps -e .

# 3. Machine configuration (.env) - created once, never overwritten.
env_args=(env init --role "$role")
[[ -n "$control_url" ]] && env_args+=(--control-url "$control_url")
[[ -n "$registration_token" ]] && env_args+=(--registration-token "$registration_token")
.venv/bin/awf "${env_args[@]}"

# 4. Database schema (control plane only) and a health report.
[[ "$role" != "worker" ]] && .venv/bin/awf db upgrade
.venv/bin/awf doctor || true
echo
echo "Start this machine with:  .venv/bin/awf start"
