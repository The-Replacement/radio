#!/usr/bin/env bash
# Hourly refresh of Mario (the host voice) time buckets. Run from cron:
#   0 * * * * /home/kbdejavu/servers/radio/scripts/mario-refresh.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Source .env so MARIO_MODEL (and friends) resolve to the box's real values.
if [ -f "$ROOT/.env" ]; then
  set -a
  . "$ROOT/.env"
  set +a
fi

MARIO_MUSIC_DIR="$ROOT/music" MARIO_HOST_DIR="$ROOT/data/host" \
  mario generate --music-dir "$ROOT/music" --host-dir "$ROOT/data/host" \
  >/dev/null 2>&1 || true