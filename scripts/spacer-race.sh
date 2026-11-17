#!/usr/bin/env bash
# spacer-race.sh — a vanished spacer must be a skipped spacer, not a dead
# stream (#123).
#
# The race: the hourly TTS prune unlinks a spacer file between the
# file.exists() guard and single()'s EOF re-fetch. single() is statically
# infallible when its file exists at wake-up (request.is_static in
# src/libs/request.liq), so that re-fetch raises into the clock and kills the
# container. Production signature: "Infallible source.dynamic single was not
# able to prepare a source in time".
#
# Three probes run against the pinned container, each in a THROWAWAY
# liquidsoap process (the production PID 1 is never touched), against temp
# files under /tmp inside the container (never /host, never /music):
#
#   (a) negative control — infallible single(), file deleted mid-play before
#       the EOF re-fetch: the abort MUST appear. If it does not, this
#       harness cannot detect the bug and every green below is meaningless.
#   (b) safe_single, file deleted BEFORE start: the skip warning MUST name
#       the path, the next source MUST play, and there MUST be no abort.
#   (c) safe_single, file deleted MID-PLAY: the spacer MUST end cleanly, the
#       next source MUST play, and there MUST be no abort.
#
# The safe_single helper is extracted from the radio.liq under test, so the
# probe can never drift from what is deployed.
#
# Usage: spacer-race.sh [path/to/radio.liq]
#   Default: <repo>/liquidsoap/radio.liq — on the box that is the deployed
#   file, so this gate validates what actually runs.
# Exit 0 only if all three probes behave as expected.

set -euo pipefail

CONTAINER="${SPACER_RACE_CONTAINER:-radio-liquidsoap}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIQ_FILE="${1:-$SCRIPT_DIR/../liquidsoap/radio.liq}"

REMOTE_DIR="/tmp/spacer-race"
SPACER="$REMOTE_DIR/spacer.wav"
NEXT="$REMOTE_DIR/next.wav"
PROBE_LIQ="$REMOTE_DIR/probe.liq"
PROBE_LOG="$REMOTE_DIR/probe.log"
PROBE_PID="$REMOTE_DIR/probe.pid"

# The production death line (#123). Everything this script exists to prevent
# ends with this string.
ABORT_SIG='Infallible source.dynamic single was not able to prepare a source in time'

FAILURES=0
HELPER=""

log() { printf '%s\n' "$*"; }

die() {
  printf 'spacer-race: %s\n' "$*" >&2
  exit 2
}

container_sh() {
  docker exec "$CONTAINER" sh -c "$1"
}

# Strip the boot noise so a failure dump is readable in a terminal.
dump_log_tail() {
  container_sh "grep -vE 'ffmpeg.filter|Using:|Loading|converter|text:3|frame:3|sandbox|startup|LOG START' '$PROBE_LOG' 2>/dev/null | tail -25" || true
}

wait_for_log() { # $1 = extended regex, $2 = deadline in seconds
  local deadline=$((SECONDS + $2))
  while ((SECONDS < deadline)); do
    if container_sh "grep -qE '$1' '$PROBE_LOG' 2>/dev/null"; then
      return 0
    fi
    sleep 1
  done
  return 1
}

log_has_abort() {
  container_sh "grep -qE '$ABORT_SIG' '$PROBE_LOG' 2>/dev/null"
}

start_probe() { # probe script arrives on stdin
  container_sh "mkdir -p '$REMOTE_DIR'"
  docker exec -i "$CONTAINER" sh -c "cat > '$PROBE_LIQ'"
  container_sh "rm -f '$PROBE_LOG' '$PROBE_PID'"
  docker exec -d "$CONTAINER" sh -c "liquidsoap '$PROBE_LIQ' > '$PROBE_LOG' 2>&1 & echo \$! > '$PROBE_PID'"
}

stop_probe() {
  # Read the pid, remove the file immediately (the EXIT trap must never
  # re-kill a stale pid), and only signal it if the target still carries
  # our probe path: a recycled pid belongs to someone else.
  container_sh "[ -f '$PROBE_PID' ] || exit 0
pid=\$(cat '$PROBE_PID')
rm -f '$PROBE_PID'
case \"\$(tr '\\0' ' ' < /proc/\$pid/cmdline 2>/dev/null)\" in
  *'/tmp/spacer-race/probe.liq'*) kill -INT \"\$pid\" 2>/dev/null; sleep 1; kill -9 \"\$pid\" 2>/dev/null;;
esac
true"
}

# Leftovers from a crashed run must die before a new one starts. Only
# liquidsoap processes carrying our probe path qualify. Matching the bare
# path would match THIS scanner's own cmdline (the pattern is literal text
# in it) and it would kill itself: docker exec would return 137 and set -e
# would take the whole script down — that is exactly what the first run did.
# The liquidsoap prefix is what makes the scanner immune; PID 1 runs
# /etc/radio.liq and never matches either way.
kill_leftovers() {
  container_sh 'for p in /proc/[0-9]*; do cmd=$(tr "\0" " " < "$p/cmdline" 2>/dev/null); case "$cmd" in liquidsoap*"/tmp/spacer-race/probe.liq"*) kill -9 "${p#/proc/}" 2>/dev/null;; esac; done; true'
}

# Read-only from /host; everything else lives under /tmp in the container.
# Smallest wav = shortest playback = fastest probes.
setup_files() {
  local src
  src="$(container_sh 'ls -S /host/*.wav 2>/dev/null | tail -1')" || true
  [ -n "$src" ] || die "no /host/*.wav found in $CONTAINER"
  container_sh "mkdir -p '$REMOTE_DIR' && cp '$src' '$SPACER' && cp '$src' '$NEXT'"
}

# (a) Negative control. The file MUST exist at wake-up — that is what makes
# single() statically infallible — so it is deleted only after playback has
# started: the production window (guard passed, re-fetch ahead). Without this
# probe proving the harness can see the abort, the other two greens mean
# nothing.
probe_negative_control() {
  log "--- (a) negative control: infallible single(), delete mid-play -> MUST abort"
  setup_files
  start_probe <<'PROBE'
settings.log.level := 3
seq = sequence([single("/tmp/spacer-race/spacer.wav"), blank(duration=60.)])
output.dummy(seq)
PROBE
  if wait_for_log 'Prepared "/tmp/spacer-race/spacer.wav"' 20; then
    container_sh "rm -f '$SPACER'"
    if wait_for_log "$ABORT_SIG" 40; then
      log "  PASS: abort reproduced — the harness can detect the bug"
    else
      log "  FAIL: no abort — the harness cannot detect the bug"
      dump_log_tail
      FAILURES=$((FAILURES + 1))
    fi
  else
    log "  FAIL: the spacer never started playing"
    dump_log_tail
    FAILURES=$((FAILURES + 1))
  fi
  stop_probe
  return 0
}

# (b) The prune wins before the fetch: safe_single must name the skip and
# playout must move on to the next source.
probe_prestart_delete() {
  log "--- (b) safe_single, file deleted BEFORE start -> warning + next plays + no abort"
  setup_files
  container_sh "rm -f '$SPACER'"
  start_probe <<PROBE
settings.log.level := 3
$HELPER
seq = sequence([safe_single("$SPACER"), single("$NEXT")])
output.dummy(seq)
PROBE
  local ok=1
  if wait_for_log "spacer unavailable, skipping: $SPACER" 20; then
    log "  PASS: skip warning names the path"
  else
    log "  FAIL: no skip warning"
    dump_log_tail
    ok=0
  fi
  if wait_for_log "Decoding \"$NEXT\" ended" 30; then
    log "  PASS: next source played (playout advanced)"
  else
    log "  FAIL: next source never played"
    dump_log_tail
    ok=0
  fi
  sleep 2
  if log_has_abort; then
    log "  FAIL: clock aborted"
    dump_log_tail
    ok=0
  else
    log "  PASS: no abort"
  fi
  stop_probe
  [ "$ok" -eq 1 ] || FAILURES=$((FAILURES + 1))
  return 0
}

# (c) The prune lands while the spacer plays: the open fd keeps the read
# alive, so the file must end cleanly and playout must move on.
probe_midplay_delete() {
  log "--- (c) safe_single, file deleted MID-PLAY -> clean EOF + next plays + no abort"
  setup_files
  start_probe <<PROBE
settings.log.level := 3
$HELPER
seq = sequence([safe_single("$SPACER"), single("$NEXT")])
output.dummy(seq)
PROBE
  local ok=1
  if wait_for_log 'Prepared "/tmp/spacer-race/spacer.wav"' 20; then
    container_sh "rm -f '$SPACER'"
    if wait_for_log "Decoding \"$SPACER\" ended" 40; then
      log "  PASS: spacer ended cleanly after the unlink"
    else
      log "  FAIL: spacer never ended cleanly"
      dump_log_tail
      ok=0
    fi
    if wait_for_log "Decoding \"$NEXT\" ended" 40; then
      log "  PASS: next source played (playout advanced)"
    else
      log "  FAIL: next source never played"
      dump_log_tail
      ok=0
    fi
    sleep 2
    if log_has_abort; then
      log "  FAIL: clock aborted"
      dump_log_tail
      ok=0
    else
      log "  PASS: no abort"
    fi
  else
    log "  FAIL: the spacer never started playing"
    dump_log_tail
    ok=0
  fi
  stop_probe
  [ "$ok" -eq 1 ] || FAILURES=$((FAILURES + 1))
  return 0
}

# Invoked via trap cleanup EXIT; SC2329 cannot see trap registration.
# shellcheck disable=SC2329
cleanup() {
  stop_probe 2>/dev/null || true
  kill_leftovers 2>/dev/null || true
  container_sh "rm -rf '$REMOTE_DIR'" 2>/dev/null || true
}

main() {
  log "spacer-race: container=$CONTAINER"
  log "radio.liq:   $LIQ_FILE"

  docker inspect -f '{{.State.Status}}' "$CONTAINER" 2>/dev/null | grep -q running \
    || die "container $CONTAINER is not running"
  [ -f "$LIQ_FILE" ] || die "no such file: $LIQ_FILE"

  # A restart would leave the same PID 1 cmdline, so fingerprint the start
  # time: if it changes during the run, the probes took the stream down.
  local started now_started
  started="$(docker inspect -f '{{.State.StartedAt}}' "$CONTAINER" 2>/dev/null || true)"

  HELPER="$(sed -n '/^def safe_single/,/^end$/p' "$LIQ_FILE")"
  [ -n "$HELPER" ] || die "safe_single not found in $LIQ_FILE — this file does not carry the #123 fix"
  case "$HELPER" in
    *request.dynamic*) ;;
    *) die "extracted safe_single looks wrong: no request.dynamic" ;;
  esac

  kill_leftovers

  probe_negative_control
  probe_prestart_delete
  probe_midplay_delete

  # The probes must not have touched the live clock.
  local prod
  prod="$(container_sh 'tr "\0" " " < /proc/1/cmdline' 2>/dev/null || true)"
  case "$prod" in
    *"/etc/radio.liq"*) log "production PID 1 untouched" ;;
    *)
      log "FAIL: production PID 1 changed: ${prod:-<unreadable>}"
      FAILURES=$((FAILURES + 1))
      ;;
  esac
  now_started="$(docker inspect -f '{{.State.StartedAt}}' "$CONTAINER" 2>/dev/null || true)"
  if [ "$now_started" = "$started" ]; then
    log "production container never restarted"
  else
    log "FAIL: production container restarted during the run ($started -> $now_started)"
    FAILURES=$((FAILURES + 1))
  fi

  if [ "$FAILURES" -eq 0 ]; then
    log "PASS: spacer-race 3/3 — negative control aborted; safe_single survived both race windows"
    exit 0
  fi
  log "FAIL: spacer-race — $FAILURES check(s) failed"
  exit 1
}

trap cleanup EXIT
main "$@"
