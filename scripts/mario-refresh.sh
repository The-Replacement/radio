#!/usr/bin/env bash
# Hourly refresh of Mario (the host voice) time buckets. Run from cron:
#   0 * * * * /home/kbdejavu/servers/radio/scripts/mario-refresh.sh
#
# Tracked in this repo and deployed by the post-receive hook. Edit here, push,
# and the next run uses the new version. Keep the render command unchanged:
# its flags are the contract with the mario container.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG="$ROOT/data/mario-refresh.log"
LOCK="/tmp/mario-refresh.lock"

# Keep the log from growing forever: keep the last 500 lines once it passes 1 MiB.
if [ -f "$LOG" ] && [ "$(stat -c %s "$LOG" 2>/dev/null || echo 0)" -gt 1048576 ]; then
  tail -n 500 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
fi

log() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*" >> "$LOG"; }

bucket() { tr -d '[:space:]' < "$ROOT/data/host/current-bucket" 2>/dev/null; }
bucket_age() {
  # Seconds since the marker was written; -1 if it is missing.
  if [ -f "$ROOT/data/host/current-bucket" ]; then
    echo $(( $(date +%s) - $(stat -c %Y "$ROOT/data/host/current-bucket") ))
  else
    echo -1
  fi
}

exec 9>"$LOCK"
if ! flock -n 9; then
  log "skip: lock held (another run still rendering); bucket=$(bucket) age=$(bucket_age)s"
  exit 0
fi

age=$(bucket_age)
age=${age:-0}
before=$(bucket)
if [ "$age" -ge 0 ] && [ "$age" -gt 4500 ]; then
  log "WARN stale bucket: ${before:-<none>} is ${age}s old (>4500s); a previous run likely overran"
fi

start=$(date +%s)
if docker compose -f "$ROOT/docker-compose.yml" run --rm mario \
  generate --music-dir /music --host-dir /data/host --liners-preset /app/presets/liners.yaml --weather-file /data/host/weather.json --keep-buckets 48 \
  >> "$LOG" 2>&1; then
  rc=0
else
  rc=$?
  log "FAIL: mario generate exited $rc"
fi
dur=$(( $(date +%s) - start ))
after=$(bucket)
log "run: before=${before:-<none>} after=${after:-<none>} duration=${dur}s rc=$rc"

exit $rc