#!/usr/bin/env bash
# Proves a stranger can get from `git clone` to a working demo.
#   scripts/fresh_clone_check.sh [repo-path-or-url]      (default: this checkout, committed state)
# Steps: clone -> venv + install -> (if Docker is available) `make up` and probe api/web
#        -> `make demo ARGS="--fake --yes"`. Prints the elapsed time; the target is < 10 minutes.
# Without Docker the stack step is skipped and says so; it does not pretend to have passed.
set -euo pipefail

SRC="${1:-$(git rev-parse --show-toplevel)}"
WORK="$(mktemp -d)"
START="$(date +%s)"
STACK=0

cleanup() {
  if [ "$STACK" = 1 ]; then (cd "$WORK/conductor" && docker compose down -v >/dev/null 2>&1) || true; fi
  [ "${KEEP:-0}" = 1 ] || rm -rf "$WORK"
}
trap cleanup EXIT

step() { printf '\n==> %s\n' "$*"; }

step "clone $SRC"
git clone --quiet "$SRC" "$WORK/conductor"
cd "$WORK/conductor"

step "python environment"
PYBIN=""
for c in python3 python; do   # skip the Windows Store shim, which exists but cannot run
  if "$c" -c 'import sys; sys.exit(sys.version_info < (3, 11))' >/dev/null 2>&1; then PYBIN="$c"; break; fi
done
[ -n "$PYBIN" ] || { echo "Python 3.11+ is required"; exit 1; }
"$PYBIN" -m venv .venv
# shellcheck disable=SC1091
if [ -f .venv/bin/activate ]; then . .venv/bin/activate; else . .venv/Scripts/activate; fi
pip install --quiet --upgrade pip
pip install --quiet -e .

if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  step "make up (api, worker, beat, redis, postgres, chroma, web)"
  [ -f .env ] || cp .env.example .env
  make up
  STACK=1
  docker compose ps
  curl -fsS http://localhost:8000/health >/dev/null && echo "api healthy"
  curl -fsS http://localhost:8080/ >/dev/null && echo "web healthy"
else
  step "SKIPPED make up: Docker is not available here (the compose stack was NOT verified)"
fi

step "make demo ARGS=\"--fake --yes\""
make demo ARGS="--fake --yes"

printf '\nfresh clone check passed in %ss\n' "$(( $(date +%s) - START ))"
