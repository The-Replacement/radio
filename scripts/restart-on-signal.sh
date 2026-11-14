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
LOCK="/tmp/restart-on-signal.lock"
LOG="$ROOT/data/restart-on-signal.log"
# A render takes ~35 min on this box; a hung one must not wedge the pipeline.
RENDER_TIMEOUT=900

log() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*" >> "$LOG"; }

[ -f "$SIGNAL" ] || exit 0

sig="$(cat "$SIGNAL" 2>/dev/null || true)"
last="$(cat "$MARK" 2>/dev/null || true)"

if [ -z "$sig" ] || [ "$sig" = "$last" ]; then
  exit 0
fi

# One handler at a time. cron ticks every minute and the render outlives the
# tick, so the marker below is stale for as long as the render runs: without
# this lock every tick spawned another `docker compose run mario` and they
# piled up (30 concurrent renders at ~30% CPU each saturated an 8-core box,
# load 98, Liquidsoap starved into "latency too high"). A skipped tick is
# safe: the in-flight handler restarts Liquidsoap with whatever library is
# current by the time it finishes.
exec 9>"$LOCK"
if ! flock -n 9; then
  log "skip: handler already running for signal $sig"
  exit 0
fi

# Pre-render Mario (the host voice) segments BEFORE restarting Liquidsoap, so
# the new library's transitions find their cached segments on air. Mario runs
# as a container (`docker compose run --rm mario`), mirroring indiegenerator.
# Keep only the current (new) bucket. A failure here must NOT block the
# restart: the segments are optional (Liquidsoap skips misses).
start=$(date +%s)
# A run killed mid-render leaves the named one-off behind (SIGKILL to the
# compose client does not stop the container); the next `--name` would then
# fail. We hold the lock, so any leftover with that name is ours to reap.
docker rm -f mario-render >/dev/null 2>&1 || true
timeout "$RENDER_TIMEOUT" docker compose -f "$ROOT/docker-compose.yml" run --rm \
  --name mario-render mario \
  generate --music-dir /music --host-dir /data/host --liners-preset /app/presets/liners.yaml --weather-file /data/host/weather.json --keep-buckets 1 \
  >/dev/null 2>&1
rc=$?
if [ "$rc" -eq 0 ]; then
  log "render ok: signal $sig duration=$(( $(date +%s) - start ))s"
else
  # 124 = timeout. `timeout` kills the compose client, not the container: reap
  # the one-off by name or it keeps rendering (and burning CPU) forever.
  [ "$rc" -eq 124 ] && docker kill mario-render >/dev/null 2>&1
  log "WARN render rc=$rc: signal $sig duration=$(( $(date +%s) - start ))s; continuing to restart"
fi

# Best-effort ownership repair for bind-mounted outputs (#83). Requires scoped
# passwordless sudo for this script on hosts where root-owned artifacts recur.
if [ -x "$ROOT/scripts/fix-data-ownership.sh" ]; then
  sudo -n "$ROOT/scripts/fix-data-ownership.sh" >/dev/null 2>&1 || true
fi

docker compose -f "$ROOT/docker-compose.yml" restart liquidsoap
printf '%s' "$sig" > "$MARK"
