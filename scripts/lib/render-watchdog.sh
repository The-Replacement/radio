#!/usr/bin/env bash
# One mario render, guarded by a progress watchdog instead of a duration bound.
#
# Why not `timeout N`: the render time is a function of the cpus cap in
# docker-compose.yml and of the library size (transitions are O(n^2)). A fixed
# duration is therefore silently coupled to two files. It bit us on
# 2026-11-14: 900 s was sized for a ~360 s render at 4 cpus, and under the
# previous 1.5-cpu cap the same render took ~2100 s, so every rotation's
# pre-render was killed at 900 s (rc=124 in data/restart-on-signal.log) and the
# new library went on air without its transition segments. Slow is not hung.
#
# Segments are written one at a time, so the newest WAV mtime is a liveness
# signal that does not care about the cap or the library size. Only a render
# that stops producing for `stall` seconds is treated as hung.
#
# Detection granularity is the 10 s sample below, so `stall` must be well above
# it (the default callers pass 240 s). A cache-hit render (everything already
# rendered) simply exits; the watchdog never fires.
#
# Prints one verdict line: "duration=<s> stalled=<bool> rc=<n>".
# Returns the render's exit status (124 when the client refused to die).

# shellcheck shell=bash

render_with_watchdog() {
  local compose="$1" host_dir="$2" hard_max="$3" stall="$4"
  shift 4

  local start pid last now newest stalled=false rc
  start=$(date +%s)

  # A previous run killed mid-render leaves the named one-off behind (SIGKILL to
  # the compose client does not stop the container); the next `--name` would
  # then fail. The caller holds the lock, so any leftover is ours to reap.
  docker rm -f mario-render >/dev/null 2>&1 || true

  # 8>&- 9>&-: the client must not inherit the callers' lock fds. If it does and
  # the script exits while the client survives, the orphan holds both locks and
  # every future run skips until it dies.
  local out="/dev/null"
  [ -n "${RENDER_OUTPUT_LOG:-}" ] && out="$RENDER_OUTPUT_LOG"
  # Optional entrypoint override, so a caller can run two mario commands in one
  # container (e.g. fetch the weather snapshot, then render the weather slot)
  # instead of paying container startup twice per interval.
  local extra=()
  [ -n "${RENDER_ENTRYPOINT:-}" ] && extra=(--entrypoint "$RENDER_ENTRYPOINT")
  # shellcheck disable=SC2086
  timeout "$hard_max" docker compose -f "$compose" run --rm --name mario-render \
    "${extra[@]}" mario "$@" >>"$out" 2>&1 8>&- 9>&- &
  pid=$!

  last=$(date +%s)
  while kill -0 "$pid" 2>/dev/null; do
    sleep 10
    newest=$(find "$host_dir" -maxdepth 1 -name '*.wav' -printf '%T@\n' 2>/dev/null \
      | sort -rn | head -1 | cut -d. -f1)
    now=$(date +%s)
    if [ -n "${newest:-}" ] && [ "$newest" -gt "$last" ]; then
      last=$newest
    fi
    if [ $(( now - last )) -ge "$stall" ]; then
      stalled=true
      docker kill mario-render >/dev/null 2>&1 || true
      break
    fi
  done

  # After a kill, do not block on a client that will not die: that would hold
  # the caller's lock for the whole hard-max window.
  if [ "$stalled" = true ]; then
    local _
    for _ in $(seq 1 12); do
      kill -0 "$pid" 2>/dev/null || break
      sleep 5
    done
  fi
  if kill -0 "$pid" 2>/dev/null; then
    rc=124
  else
    wait "$pid"
    rc=$?
  fi

  printf 'duration=%ss stalled=%s rc=%s\n' \
    "$(( $(date +%s) - start ))" "$stalled" "$rc"
  return "$rc"
}
