#!/usr/bin/env bash
# AC1 soak monitor (SDD #65): count liquidsoap "cannot decode" log lines over a
# sustained window (default 60 min). Writes periodic samples and final summary.
# Usage: ./scripts/ac1-soak.sh [duration_seconds] [interval_seconds]
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 1

DUR="${1:-3600}"
INT="${2:-300}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="$ROOT/data/ac1-soak-${STAMP}.log"
STATE="$ROOT/data/ac1-soak-${STAMP}.pid"

echo $$ > "$STATE"
start_epoch="$(date +%s)"
end_epoch=$((start_epoch + DUR))

log() { printf '[%s] %s\n' "$(date -u +%FT%TZ)" "$*" | tee -a "$OUT"; }

log "AC1 soak start: duration=${DUR}s interval=${INT}s out=$OUT"
log "repo=$(git rev-parse --short HEAD 2>/dev/null || echo unknown) host=$(hostname)"
log "liquidsoap_service=$(docker compose ps --status running --services 2>/dev/null | grep -x liquidsoap || echo missing)"

count_decode() {
  docker compose logs --no-log-prefix liquidsoap 2>/dev/null | grep -c 'cannot decode' || true
}

baseline_total="$(count_decode)"
baseline_total="${baseline_total:-0}"
log "baseline_cannot_decode_total=${baseline_total}"

i=0
while :; do
  now="$(date +%s)"
  [[ "$now" -ge "$end_epoch" ]] && break
  i=$((i+1))
  window_total="$(count_decode)"
  window_total="${window_total:-0}"
  delta=$((window_total - baseline_total))
  running="$(docker compose ps --status running --services 2>/dev/null | grep -x liquidsoap || true)"
  log "sample=${i} elapsed=$((now-start_epoch))s liquidsoap_running=${running:-no} cannot_decode_total=${window_total} delta_since_start=${delta}"
  # capture recent decode-related lines if any appeared
  if [[ "$delta" -gt 0 ]]; then
    docker compose logs --tail=50 --no-log-prefix liquidsoap 2>/dev/null | grep 'cannot decode' | tail -n 5 | sed 's/^/[decode] /' >> "$OUT" || true
  fi
  sleep "$INT" || true
done

final_total="$(count_decode)"
final_total="${final_total:-0}"
final_delta=$((final_total - baseline_total))
log "AC1 soak end: cannot_decode_total=${final_total} delta_since_start=${final_delta}"
if [[ "$final_delta" -eq 0 ]]; then
  log "RESULT=PASS (no 'cannot decode' observed during soak window)"
  rc=0
else
  log "RESULT=FAIL (observed ${final_delta} 'cannot decode' line(s) during soak window)"
  rc=1
fi
rm -f "$STATE"
exit "$rc"
