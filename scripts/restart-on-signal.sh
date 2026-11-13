#!/usr/bin/env bash
# Consumes the restart signal the generator writes when it rotates the library.
#
# The generator runs with INDIEGEN_RESTART=signal:/var/lib/indiegen/restart-request
# (volume ./data/indiegen). When a cycle ends it writes a timestamp there; this
# script reads it and, if it changed since last time, runs `docker compose restart
# liquidsoap` — necessary because liquidsoap's watch adds tracks but does not
# forget deleted ones (the "phantom" deletions would keep playing).
#
# It runs from cron every minute, as the box's user (kbdejavu, in the docker
# group). There is no /var/run/docker.sock in any container: the generator cannot
# touch anything but write a file; the restart is the radio's decision, executed
# by the host.
#
#   * * * * * /home/kbdejavu/servers/radio/scripts/restart-on-signal.sh
#
# Idempotent: if there is no new signal, it touches nothing. The marker is kept
# next to the signal (data/indiegen/.last-restart, gitignored).

set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SIGNAL="$ROOT/data/indiegen/restart-request"
MARK="$ROOT/data/indiegen/.last-restart"

[ -f "$SIGNAL" ] || exit 0

sig="$(cat "$SIGNAL" 2>/dev/null || true)"
last="$(cat "$MARK" 2>/dev/null || true)"

if [ -z "$sig" ] || [ "$sig" = "$last" ]; then
  exit 0
fi

# Pre-render Mario (the host voice) segments BEFORE restarting Liquidsoap, so
# the new library's transitions find their cached segments on air. Mario runs
# as a container (`docker compose run --rm mario`), mirroring indiegenerator.
# Keep only the current (new) bucket. A failure here must NOT block the
# restart: the segments are optional (Liquidsoap skips misses).
docker compose -f "$ROOT/docker-compose.yml" run --rm mario \
  generate --music-dir /music --host-dir /data/host --liners-preset /app/presets/liners.yaml --weather-file /data/host/weather.json --keep-buckets 1 \
  >/dev/null 2>&1 || true

docker compose -f "$ROOT/docker-compose.yml" restart liquidsoap
printf '%s' "$sig" > "$MARK"
