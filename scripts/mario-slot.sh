#!/usr/bin/env bash
# Refresh ONE mario slot on its own cadence, independent of the time bucket.
#
#   3,13,23,33,43,53 * * * * .../scripts/mario-slot.sh weather
#   7,37 * * * * .../scripts/mario-slot.sh rss
#
# Offsets are deliberate: the hourly job starts at :00 and a bucket flip keeps it
# rendering for ~6.5 min, so a slot run at :00 would only lose the render lock.
#
# Why separate from mario-refresh.sh: transitions are O(n^2) per track pair and
# must NOT run every 10 minutes, while weather and news go stale if they wait
# for the bucket to flip. The slot renders are single segments (~3 s of TTS at
# 4 cpus) plus container startup.
#
# Shares the render lock with the hourly refresh and the rotation handler, so
# only one mario render runs at a time, and shares the box gate, so a hot or
# busy host defers the refresh instead of stacking on top of it.
set -uo pipefail

SLOT="${1:-}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG="$ROOT/data/mario-slot.log"
RENDER_LOCK="/tmp/mario-refresh.lock"
RENDER_STALL_SECONDS="${MARIO_RENDER_STALL_SECONDS:-120}"
RENDER_HARD_MAX="${MARIO_RENDER_HARD_MAX:-3600}"

log() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*" >> "$LOG"; }

case "$SLOT" in
  weather|rss) ;;
  *)
    log "FAIL: unknown slot '${SLOT}' (expected weather or rss)"
    exit 2
    ;;
esac

# shellcheck source=scripts/lib/render-watchdog.sh
. "$ROOT/scripts/lib/render-watchdog.sh"

# Keep the log bounded: 144 weather runs a day add up.
if [ -f "$LOG" ] && [ "$(stat -c %s "$LOG" 2>/dev/null || echo 0)" -gt 1048576 ]; then
  tail -n 500 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
fi

exec 9>"$RENDER_LOCK"
if ! flock -n 9; then
  log "skip $SLOT: render lock held (hourly refresh or rotation handler rendering)"
  exit 0
fi

if ! gate=$("$ROOT/scripts/radio-job.sh" preflight 2>&1); then
  log "skip $SLOT: ${gate#gate closed: }"
  exit 0
fi

# One container per interval: for weather, refresh the snapshot and render in
# the same run (a second container would double the startup cost, and startup
# dominates a single-segment render). A failed fetch is loud in the log but does
# not abort: the previous snapshot is still valid and the render then just
# cache-hits.
RENDER_OUTPUT_LOG="$LOG"
if [ "$SLOT" = "weather" ]; then
  RENDER_ENTRYPOINT=sh
  SLOT_ARGS=(-c "mario weather-fetch --out /data/host/weather.json || echo WEATHER_FETCH_FAILED >&2; exec mario generate --music-dir /music --host-dir /data/host --liners-preset /app/presets/liners.yaml --weather-file /data/host/weather.json --slots weather --no-marker --keep-buckets 1")
else
  # News rotates over the feeds by bucket seed, so a 30-minute cadence needs a
  # 30-minute bucket or the same headline repeats until the 6-hour bucket flips.
  # --no-marker keeps that narrow bucket out of current-bucket, which playout
  # uses to build every transition and liner path.
  half=$(date -u +%H)
  [ "$(date -u +%M)" -ge 30 ] && half="$half-30" || half="$half-00"
  RSS_BUCKET="$(date -u +%Y-%m-%d)T${half}"
  SLOT_ARGS=(generate --music-dir /music --host-dir /data/host
    --liners-preset /app/presets/liners.yaml --weather-file /data/host/weather.json
    --slots rss --time-bucket "$RSS_BUCKET" --no-marker --keep-buckets 1)
fi

verdict=$(render_with_watchdog "$ROOT/docker-compose.yml" "$ROOT/data/host" \
  "$RENDER_HARD_MAX" "$RENDER_STALL_SECONDS" "${SLOT_ARGS[@]}")
rc=$?

if [ "$rc" -eq 0 ]; then
  # The alias mtime is what radio-job's freshness check reads, and a republish
  # is skipped when the content did not change (same hash -> same target). Touch
  # it so the age means "last successful refresh", not "last time the weather
  # moved": a stable reading for 25 minutes is healthy, not stale.
  alias="$ROOT/data/host/${SLOT}.wav"
  if [ -L "$alias" ] || [ -e "$alias" ]; then
    touch -h "$alias" 2>/dev/null || true
  fi
  log "ok $SLOT: $verdict"
else
  log "WARN $SLOT: $verdict"
fi
exit "$rc"
