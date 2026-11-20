#!/usr/bin/env bash
# token.sh - thin wrapper for the circle-door CLI.
#
# Keeps existing usage working (no silent breakage) while token management is
# provided by the standalone, dependency-light Python CLI (`circle-door`).
#
# Usage (equivalent to circle-door):
#   ./scripts/token.sh add <name> [expiry]
#   ./scripts/token.sh reveal <name>
#   ./scripts/token.sh rm <name>
#   ./scripts/token.sh ls
#   ./scripts/token.sh regen
#   ./scripts/token.sh rotate <name> [expiry]
#   ./scripts/token.sh prune
#   ./scripts/token.sh check
#
# Environment seams honored by the CLI: DOOR_TOKEN_FILE, DOOR_MATCHER_FILE,
# DOOR_COMPOSE, CADDY_IMAGE, and .env values read by the implementation.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Prefer an installed console script when available.
if command -v circle-door >/dev/null 2>&1; then
  exec circle-door "$@"
fi

# Fallback: run from this repo's source (stdlib-only CLI path).
if command -v python3 >/dev/null 2>&1; then
  export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
  exec python3 -m circle_door "$@"
fi

echo "ERROR: python3 is required to run circle-door (or install the 'circle-door' CLI)." >&2
exit 1
