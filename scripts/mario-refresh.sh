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

# A hung render must not hold this lock forever, and a slow one is not a hang
# (the render time depends on the cpus cap and on the library size). Shared with
# scripts/restart-on-signal.sh.
RENDER_STALL_SECONDS="${MARIO_RENDER_STALL_SECONDS:-240}"
RENDER_HARD_MAX="${MARIO_RENDER_HARD_MAX:-14400}"
# shellcheck source=scripts/lib/render-watchdog.sh
. "$ROOT/scripts/lib/render-watchdog.sh"

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
# Same guard as the rotation handler: a hung render must not hold this lock
# forever (every later hour would log "skip: lock held" and the host voice would
# freeze at the last bucket, silently). See scripts/lib/render-watchdog.sh.
# RENDER_OUTPUT_LOG keeps the segment list in this log, as it was before.
RENDER_OUTPUT_LOG="$LOG"
verdict=$(render_with_watchdog "$ROOT/docker-compose.yml" "$ROOT/data/host" \
  "$RENDER_HARD_MAX" "$RENDER_STALL_SECONDS" \
  generate --music-dir /music --host-dir /data/host \
  --liners-preset /app/presets/liners.yaml --weather-file /data/host/weather.json \
  --keep-buckets 1)
rc=$?
if [ "$rc" -ne 0 ]; then
  log "WARN: mario generate exited $rc ($verdict)"
fi
# Best-effort ownership repair for bind-mounted outputs (#83). Requires scoped
# passwordless sudo for this script on hosts where root-owned artifacts recur.
# The repair needs passwordless sudo scoped to that one script (#83). When it is
# not wired, `sudo -n` fails with "interactive authentication is required" and
# the `|| true` turned that into silence: the mitigation looked alive for days
# while never running once. Probe first and say so.
if [ -x "$ROOT/scripts/fix-data-ownership.sh" ]; then
  if sudo -n true 2>/dev/null; then
    sudo -n "$ROOT/scripts/fix-data-ownership.sh" >> "$LOG" 2>&1 \
      || log "WARN ownership repair failed"
  else
    log "note: ownership repair skipped (no passwordless sudo for this script)"
  fi
fi
dur=$(( $(date +%s) - start ))
after=$(bucket)
log "run: before=${before:-<none>} after=${after:-<none>} duration=${dur}s rc=$rc"

exit $rc