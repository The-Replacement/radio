#!/usr/bin/env bash
# Hourly refresh of Mario (the host voice) time buckets. Run from cron:
#   0 * * * * /home/kbdejavu/servers/radio/scripts/mario-refresh.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Mario runs as a container (mirroring indiegenerator).
docker compose -f "$ROOT/docker-compose.yml" run --rm mario \
  generate --music-dir /music --host-dir /data/host --liners-preset /app/presets/liners.yaml --weather-file /data/host/weather.json \
  >/dev/null 2>&1 || true