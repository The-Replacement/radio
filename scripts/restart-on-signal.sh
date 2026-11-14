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
# The hourly refresh (scripts/mario-refresh.sh) renders the same image and holds
# this lock for the whole render. Sharing it keeps two renders from running at
# once: same segments, twice the CPU.
RENDER_LOCK="/tmp/mario-refresh.lock"
LOG="$ROOT/data/restart-on-signal.log"
HOST_DIR="$ROOT/data/host"
# A hung render must not wedge the pipeline, but a *slow* one is not a hang.
# The old absolute timeout was silently coupled to the cpus cap in
# docker-compose.yml: 900 s was sized for a ~360 s render at 4 cpus, and under
# the previous 1.5-cpu cap the same render took ~2100 s, so every rotation was
# killed at 900 s (rc=124 in this log, 2026-11-14T10:45) and the new library
# went on air without its transition segments. Watch progress instead: a render
# that still writes segments is working no matter what the cap is.
RENDER_STALL_SECONDS="${MARIO_RENDER_STALL_SECONDS:-240}"
# Backstop against a bug in the watchdog, not a cap-coupled limit.
RENDER_HARD_MAX="${MARIO_RENDER_HARD_MAX:-14400}"

log() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*" >> "$LOG"; }

# shellcheck source=scripts/lib/render-watchdog.sh
. "$ROOT/scripts/lib/render-watchdog.sh"

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
# Two reasons to skip the render, both logged: the hourly refresh is already
# rendering (same lock), or the box's own gate says not now (hot, memory
# pressure, stream sick). Skipping is safe by design — the segments are
# optional and Liquidsoap skips misses — while the restart below is not.
render_skip=""
exec 8>"$RENDER_LOCK"
if ! flock -n 8; then
  render_skip="hourly refresh is rendering ($RENDER_LOCK held)"
elif ! gate=$("$ROOT/scripts/radio-job.sh" preflight 2>&1); then
  render_skip="gate closed: ${gate#gate closed: }"
fi

if [ -n "$render_skip" ]; then
  log "skip render: $render_skip; signal $sig"
else
  # A run killed mid-render leaves the named one-off behind (SIGKILL to the
  # compose client does not stop the container); the next `--name` would then
  # fail. We hold the lock, so any leftover with that name is ours to reap.
  verdict=$(render_with_watchdog "$ROOT/docker-compose.yml" "$HOST_DIR" \
    "$RENDER_HARD_MAX" "$RENDER_STALL_SECONDS" \
    generate --music-dir /music --host-dir /data/host \
    --liners-preset /app/presets/liners.yaml --weather-file /data/host/weather.json \
    --keep-buckets 1)
  rc=$?
  if [ "$rc" -eq 0 ]; then
    log "render ok: signal $sig $verdict"
  else
    log "WARN render failed: signal $sig $verdict; continuing to restart"
  fi
fi

# Best-effort ownership repair for bind-mounted outputs (#83). Requires scoped
# passwordless sudo for this script on hosts where root-owned artifacts recur.
if [ -x "$ROOT/scripts/fix-data-ownership.sh" ]; then
  if sudo -n true 2>/dev/null; then
    sudo -n "$ROOT/scripts/fix-data-ownership.sh" >/dev/null 2>&1 \
      || log "WARN ownership repair failed"
  else
    log "note: ownership repair skipped (no passwordless sudo for this script)"
  fi
fi

docker compose -f "$ROOT/docker-compose.yml" restart liquidsoap
printf '%s' "$sig" > "$MARK"
