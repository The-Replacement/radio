#!/usr/bin/env bash
# Hourly refresh of Mario (the host voice) time buckets. Run from cron:
#   0 * * * * /home/kbdejavu/servers/radio/scripts/mario-refresh.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Serialize runs. Rendering grows with the library (N*(N-1) pairs, ~4.5s each),
# so a big library can outlive the hour. A second run must skip, not race the
# first over the same host directory.
exec 9>/tmp/mario-refresh.lock
flock -n 9 || exit 0

# Mario runs as a container (mirroring indiegenerator).
docker compose -f "$ROOT/docker-compose.yml" run --rm mario \
  generate --music-dir /music --host-dir /data/host --liners-preset /app/presets/liners.yaml --weather-file /data/host/weather.json \
  >/dev/null 2>&1 || true