#!/usr/bin/env bash
# radio-job - the appliance's single entry point for scheduled work.
#
#   ./scripts/radio-job.sh gc        # retention: bound everything that grows
#   ./scripts/radio-job.sh verify    # probe the station, write data/status.json
#   ./scripts/radio-job.sh status    # print that file as text
#
# WHY A SINGLE ENTRY POINT
# Today the box runs three independent schedules (a per-minute watcher, an hourly
# TTS cron and a six-hour generator loop) and nothing bounds its own storage. A
# 21 GB Docker build cache and an unbounded journal are what turn "it runs fine"
# into "it stopped publishing and nobody knows why".
#
# Tracking: #103 (gc), #104 (status). Later subcommands (render, tts, rotate) and
# the thermal/memory gate land with #105; the self-heal ladder with #106.
#
# English only, per AGENTS.md.

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA="$ROOT/data"
GC_LOG="$DATA/gc.log"
STATUS="$DATA/status.json"

# CPU capacity, so a load threshold is expressed against the real machine
# instead of a number baked to the current box.
NPROC=$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 1)

# RADIO_JOB_DRY_RUN=1 prints what gc would do and touches nothing. The first run
# of a destructive script should not be its first test.
DRY_RUN="${RADIO_JOB_DRY_RUN:-0}"

JOB_LOG="$DATA/job.log"

# Shadow mode: evaluate the gate, log what would happen, execute nothing. It is
# how the scheduler is proven against the live station before it is allowed to act.
SHADOW="${RADIO_JOB_SHADOW:-0}"

# Gate thresholds. Measured envelope (2026-11-13): idle 42-46 C, hourly TTS peak
# 72 C at the old 1.5-cpu cap, no throttle events. 78 C sat above that peak.
# Updated 2026-11-14 after the render moved to 4 cpus: the render itself reaches
# 88 C (measured, i7-1165G7), so a threshold below it closed the gate because of
# the job the gate is protecting. 92 C sits above the scheduled render's own
# peak and below Tjmax (100 C), so a closed gate now means something ELSE is
# heating the box (ambient, a runaway, another job).
TEMP_MAX_C=92
PSI_MEM_MAX=10
# Load average (1 min) at which adding another heavy job stops being free,
# as a percentage of online CPUs. Deliberately NOT PSI cpu: root-cgroup "some"
# is inflated by quota-throttled containers — a single mario render sitting at
# its own cpus cap reports ~50% "some" on a 79%-idle box (measured 2026-11-14,
# container cgroup full=52.57%, root cgroup full=0.00%). That measures the cap,
# not the load. Load 1 min was 0.2 idle, ~4.5 during a 4-cpu render, 98 during
# the stampede, so the signal separates those three cases.
LOAD_FRACTION_PCT=75
LOAD_MAX=$(awk -v n="$NPROC" -v p="$LOAD_FRACTION_PCT" 'BEGIN { printf "%.2f", n * p / 100 }')
# Weather refreshes every 10 minutes, news every 30. These are "is it still
# airing at all" limits, not precision: one skipped tick must not alarm, a dead
# cron must. A missing/dangling alias reports age -1 and counts as not fresh.
WEATHER_MAX_AGE_S=1500
RSS_MAX_AGE_S=3900
STATUS_MAX_AGE_S=600

# Share of its memory limit a container may reach before the gate stops handing
# the box more work (#102). The 2026-11-16 liquidsoap kill loop reached its
# 512 MiB cap in ~23 minutes after a rotation; a five-minute verify timer sees
# that climb well before 100 %. The siren is not the cap -- an unmeasured cap is
# -- so this is the number the operator tunes caps against.
CAP_PRESSURE_PCT=90

# Retention bounds. One place, so a policy change is not a code change.
BUILD_CACHE_KEEP="5GB"
# The age window is what actually reclaims on a box without buildx (see gc).
# 7 days outlives a debugging session, not a release cycle.
BUILD_CACHE_MAX_AGE="168h"
JOURNAL_MAX="1G"
DEPLOY_LOG_MAX_BYTES=$((10 * 1024 * 1024))
KEEP_SOAK_LOGS=10
KEEP_RUN_DIRS=50
GC_LOG_MAX_BYTES=$((1024 * 1024))

# Disk floor: below this the station stops taking on new content rather than
# discovering a full disk halfway through a publish.
DISK_FLOOR_GB=5

# The script is run by cron and by humans; a silent failure to write the status
# file is worse than a loud one, because everything downstream trusts it.
require_writable_data() {
  if ! mkdir -p "$DATA" 2>/dev/null || [ ! -w "$DATA" ]; then
    printf 'FAIL: %s is not writable by %s (owner: %s)\n' \
      "$DATA" "$(id -un)" "$(stat -c '%U:%G' "$DATA" 2>/dev/null || echo unknown)" >&2
    printf 'hint: sudo ./scripts/fix-data-ownership.sh\n' >&2
    return 1
  fi
  return 0
}

log_gc() {
  printf '%s %s\n' "$(date -u +%FT%TZ)" "$*" >> "$GC_LOG"
}

log_job() {
  printf '%s %s\n' "$(date -u +%FT%TZ)" "$*" >> "$JOB_LOG"
}

# SI units, matching `docker system df` and `df -B1`, at 4 significant digits:
# "8.735GB" is the same number docker printed, and a retention log that rounds
# 8.735GB down to "8.8GB" cannot account for every byte. Note the one place this
# reads oddly: `df -h` is IEC, so a line here saying 858.6GB is "800G" there.
human() {
  awk -v b="${1:-0}" 'BEGIN {
    split("B|kB|MB|GB|TB", u, "|"); i = 1
    while (b >= 1000 && i < 5) { b /= 1000; i++ }
    printf (i == 1 ? "%d%s\n" : "%.4g%s\n"), b, u[i]
  }'
}

# Docker reports sizes as SI strings ("8.735GB"); numfmt --from=si rejects the
# trailing B, so parse them here rather than depend on that quirk.
human_to_bytes() {
  awk -v v="$1" 'BEGIN {
    if (v == "") { print 0; exit }
    n = v + 0
    u = toupper(v); sub(/^[0-9.]+/, "", u)
    if      (u ~ /^T/) m = 1e12
    else if (u ~ /^G/) m = 1e9
    else if (u ~ /^M/) m = 1e6
    else if (u ~ /^K/) m = 1e3
    else               m = 1
    printf "%d\n", n * m
  }'
}

# One row of `docker system df`, in bytes. What docker counts per class is its
# business; gc only needs before/after.
docker_df_bytes() {
  local size
  size=$(docker system df --format '{{.Type}}|{{.Size}}' 2>/dev/null | awk -F'|' -v t="$1" '$1 == t { print $2 }')
  human_to_bytes "$size"
}

docker_df_reclaimable() {
  docker system df --format '{{.Type}}|{{.Reclaimable}}' 2>/dev/null | awk -F'|' -v t="$1" '$1 == t { print $2 }'
}

freed_bytes() { if [ "$1" -le "$2" ]; then echo 0; else echo $(($1 - $2)); fi; }

run() {
  if [ "$DRY_RUN" = "1" ]; then
    printf '[dry] %s\n' "$*"
    return 0
  fi
  "$@"
}

# ---------------------------------------------------------------- gc

rotate_if_large() {
  local file="$1" max="$2" keep_lines="$3"
  [ -f "$file" ] || return 0
  local size
  size=$(stat -c %s "$file" 2>/dev/null || echo 0)
  [ "$size" -le "$max" ] && return 0
  tail -n "$keep_lines" "$file" > "$file.tmp" && mv "$file.tmp" "$file"
  log_gc "rotated $(basename "$file"): was $(human "$size")"
}

cmd_gc() {
  require_writable_data || return 1
  local before after

  if [ -f "$GC_LOG" ] && [ "$(stat -c %s "$GC_LOG" 2>/dev/null || echo 0)" -gt "$GC_LOG_MAX_BYTES" ]; then
    tail -n 500 "$GC_LOG" > "$GC_LOG.tmp" && mv "$GC_LOG.tmp" "$GC_LOG"
  fi

  log_gc "gc start"
  before=$(df --output=avail -B1 / | tail -1)

  if docker info >/dev/null 2>&1; then
    # Build cache is the class that grows without bound. --keep-storage alone is
    # NOT enough: it is only honoured when the BuildKit builder owns the cache.
    # On the mainframe (docker 29, no buildx plugin, measured 2026-11-18) it
    # exits 0 having freed 0B while 5.1GB stayed reclaimable, so the age prune is
    # the mechanism that actually reclaims. The cap is then enforced by hand
    # when the window still leaves more than BUILD_CACHE_KEEP behind.
    local cache_before cache_after keep_bytes
    keep_bytes=$(human_to_bytes "$BUILD_CACHE_KEEP")
    cache_before=$(docker_df_bytes "Build Cache")
    if [ "$DRY_RUN" = "1" ]; then
      log_gc "dry: build cache is $(human "$cache_before"); would prune age > $BUILD_CACHE_MAX_AGE, keep $BUILD_CACHE_KEEP"
    else
      # Docker's own output is mostly the legacy-builder deprecation notice; the
      # byte accounting below is the audit trail, so only failures are surfaced.
      docker builder prune -f --keep-storage "$BUILD_CACHE_KEEP" >/dev/null 2>&1 \
        || log_gc "FAIL: builder prune --keep-storage $BUILD_CACHE_KEEP failed"
      docker builder prune -f --filter "until=$BUILD_CACHE_MAX_AGE" >/dev/null 2>&1 \
        || log_gc "FAIL: builder prune --filter until=$BUILD_CACHE_MAX_AGE failed"
      cache_after=$(docker_df_bytes "Build Cache")
      if [ "$cache_after" -gt "$keep_bytes" ]; then
        docker builder prune -af >/dev/null 2>&1 \
          && log_gc "build cache $(human "$cache_after") exceeds $BUILD_CACHE_KEEP after the age prune; pruned all"
        cache_after=$(docker_df_bytes "Build Cache")
      fi
      log_gc "build cache $(human "$cache_before") -> $(human "$cache_after") (freed $(human "$(freed_bytes "$cache_before" "$cache_after")"))"
    fi

    local img_before img_after img_reclaimable
    img_before=$(docker_df_bytes "Images")
    if [ "$DRY_RUN" = "1" ]; then
      log_gc "dry: images $(human "$img_before"); would prune dangling"
    else
      docker image prune -f >/dev/null 2>&1 || log_gc "FAIL: image prune failed"
      img_after=$(docker_df_bytes "Images")
      log_gc "images $(human "$img_before") -> $(human "$img_after") (freed $(human "$(freed_bytes "$img_before" "$img_after")"))"
      # An image a container is running from is not ours to delete: the design
      # table's "keep last 2 per service" would take out the live encoder.
      img_reclaimable=$(docker_df_reclaimable "Images")
      [ -n "$img_reclaimable" ] \
        && log_gc "images reclaimable but held by a container: $img_reclaimable (gc leaves those alone)"
    fi
  else
    log_gc "SKIP: docker daemon not reachable"
  fi

  # Journal: needs root. Absent passwordless sudo this is a line in the log, not
  # a failed run. The durable bound is SystemMaxUse=1G in journald.conf (installed
  # by infra/deploy/bootstrap.sh); the vacuum only enforces it sooner.
  local journal_now
  # journalctl prints "take up 142.5M"; the unit may or may not carry a B.
  journal_now=$(journalctl --disk-usage 2>/dev/null | sed -n 's/.*take up \([0-9.]*[A-Za-z]*\).*/\1/p')
  if [ "$DRY_RUN" = "1" ]; then
    log_gc "dry: journal is ${journal_now:-unknown}; would vacuum to $JOURNAL_MAX"
  elif sudo -n journalctl --vacuum-size="$JOURNAL_MAX" >/dev/null 2>&1; then
    log_gc "journal vacuumed to $JOURNAL_MAX (was ${journal_now:-unknown})"
  else
    log_gc "SKIP: journal vacuum needs root (was ${journal_now:-unknown}; SystemMaxUse in journald.conf is the durable fix)"
  fi

  rotate_if_large "$DATA/deploy.log" "$DEPLOY_LOG_MAX_BYTES" 2000
  rotate_if_large "$DATA/mario-refresh.log" $((1024 * 1024)) 500
  rotate_if_large "$JOB_LOG" $((1024 * 1024)) 500

  # Diagnostic artefacts: keep the newest, delete the rest by name.
  local soak
  soak=$(find "$DATA" -maxdepth 1 -name 'ac1-soak-*.log' -printf '%T@ %p\n' 2>/dev/null \
    | sort -rn | tail -n +$((KEEP_SOAK_LOGS + 1)) | cut -d' ' -f2-)
  if [ -n "$soak" ]; then
    printf '%s\n' "$soak" | while IFS= read -r f; do
      run rm -f "$f"
      log_gc "removed $(basename "$f")"
    done
  fi

  # Generator run history. Directories only, oldest first.
  local runs="$DATA/indiegen/runs"
  if [ -d "$runs" ]; then
    local stale
    stale=$(find "$runs" -mindepth 1 -maxdepth 1 -type d -printf '%T@ %p\n' 2>/dev/null \
      | sort -rn | tail -n +$((KEEP_RUN_DIRS + 1)) | cut -d' ' -f2-)
    if [ -n "$stale" ]; then
      printf '%s\n' "$stale" | while IFS= read -r d; do
        run rm -rf "$d"
        log_gc "removed run $(basename "$d")"
      done
    fi
  fi

  after=$(df --output=avail -B1 / | tail -1)
  local freed=$((after - before))
  [ "$freed" -lt 0 ] && freed=0
  log_gc "gc done, freed $(human "$freed"), free now $(human "$after")"

  printf 'gc done: freed %s, %s free on /\n' "$(human "$freed")" "$(human "$after")"
  printf 'detail: %s\n' "$GC_LOG"
}

# ---------------------------------------------------------------- verify

# Writes one JSON object. Python does the escaping: a track title with a quote in
# it must not be able to produce invalid JSON.
cmd_verify() {
  require_writable_data || return 1
  local tmp
  tmp=$(mktemp)
  # shellcheck disable=SC2064
  trap "rm -f '$tmp'" RETURN

  # --- stream: source connected, and bytes actually moving
  local icecast_json title source_present=false bytes_rate=0 tx1 tx2
  icecast_json=$(docker exec radio-caddy wget -qO- http://icecast:8000/status-json.xsl 2>/dev/null)
  title=$(printf '%s' "$icecast_json" | sed -n 's/.*"title":"\([^"]*\)".*/\1/p' | head -1)
  [ -n "$title" ] && source_present=true
  tx1=$(docker exec radio-liquidsoap cat /sys/class/net/eth0/statistics/tx_bytes 2>/dev/null)
  sleep 2
  tx2=$(docker exec radio-liquidsoap cat /sys/class/net/eth0/statistics/tx_bytes 2>/dev/null)
  if [ -n "${tx1:-}" ] && [ -n "${tx2:-}" ]; then
    case "$tx1$tx2" in ''|*[!0-9]*) ;; *) bytes_rate=$(( (tx2 - tx1) / 2 )) ;; esac
  fi

  # --- door: no token must not get through
  local door_code
  local http_port
  http_port=$(sed -n 's/^HTTP_PORT=\([0-9]*\).*/\1/p' "$ROOT/.env" 2>/dev/null | tail -1)
  [ -n "${http_port:-}" ] || http_port=8090
  door_code=$(curl -s -o /dev/null -m 5 -w '%{http_code}' "http://127.0.0.1:${http_port}/" 2>/dev/null || echo 000)

  # --- camera: newest HLS segment
  local cam_age=-1
  local newest_cam
  newest_cam=$(find "$DATA/cam" -maxdepth 1 -name '*.m3u8' -printf '%T@\n' 2>/dev/null | sort -rn | head -1)
  if [ -n "$newest_cam" ]; then
    cam_age=$(( $(date +%s) - ${newest_cam%%.*} ))
  fi

  # --- content
  local tracks catalog_ok=false last_tts_age=-1
  tracks=$(find "$ROOT/music-audio" -maxdepth 1 -name '*.flac' 2>/dev/null | wc -l)
  if python3 -c 'import json,sys; json.load(open(sys.argv[1]))' "$ROOT/music/catalog.json" 2>/dev/null; then
    catalog_ok=true
  fi
  local newest_wav
  newest_wav=$(find "$DATA/host" -maxdepth 1 -name '*.wav' -printf '%T@\n' 2>/dev/null | sort -rn | head -1)
  if [ -n "$newest_wav" ]; then
    last_tts_age=$(( $(date +%s) - ${newest_wav%%.*} ))
  fi
  # Weather and news refresh on their own cadence (10 and 30 minutes), so "is
  # this still airing?" is not answered by any WAV mtime: their alias is
  # republished on every run, so the alias mtime IS the refresh time. -1 means
  # the alias is missing or dangling, which is a broken publish, not freshness.
  local weather_age=-1 rss_age=-1 weather_fresh=false rss_fresh=false
  if [ -e "$DATA/host/weather.wav" ]; then
    weather_age=$(( $(date +%s) - $(stat -c %Y "$DATA/host/weather.wav" 2>/dev/null || echo 0) ))
  fi
  if [ -e "$DATA/host/rss.wav" ]; then
    rss_age=$(( $(date +%s) - $(stat -c %Y "$DATA/host/rss.wav" 2>/dev/null || echo 0) ))
  fi
  [ "$weather_age" -ge 0 ] && [ "$weather_age" -le "$WEATHER_MAX_AGE_S" ] && weather_fresh=true
  [ "$rss_age" -ge 0 ] && [ "$rss_age" -le "$RSS_MAX_AGE_S" ] && rss_fresh=true

  # --- resources
  local pkg_c=0 mem_avail_mb disk_free_gb mem_pressure disk_below_floor
  local tz
  tz=$(for z in /sys/class/thermal/thermal_zone*/temp; do cat "$z" 2>/dev/null; done | sort -n | tail -1)
  [ -n "${tz:-}" ] && pkg_c=$((tz / 1000))
  mem_avail_mb=$(awk '/^MemAvailable:/ {printf "%d", $2/1024}' /proc/meminfo)
  disk_free_gb=$(df --output=avail -B1G / | tail -1 | tr -d ' ')
  disk_below_floor=$([ "$disk_free_gb" -lt "$DISK_FLOOR_GB" ] && echo true || echo false)
  mem_pressure=$(awk '/^some/ {print $3}' /proc/pressure/memory 2>/dev/null | sed 's/avg60=//')

  # --- caps: every radio container must report a memory limit
  local uncapped=0
  local ids
  ids=$(docker ps -q --filter 'name=radio-' 2>/dev/null)
  if [ -n "$ids" ]; then
    # shellcheck disable=SC2086
    uncapped=$(docker inspect $ids --format '{{if eq .HostConfig.Memory 0}}{{.Name}}{{end}}' 2>/dev/null | grep -c . )
  fi

  # --- memory pressure per container (#102): how close is each one to its cap?
  # On 2026-11-16 the 512 MiB liquidsoap limit OOM-killed the stream 12 times
  # while this file said "0 uncapped". A limit that is never measured is a limit
  # that kills silently, and the kernel journal was the only record left. The
  # cgroup counters (memory.peak, memory.events) are read from inside the
  # container's own cgroup, so the answer needs no sampling and no history: the
  # five-minute verify timer turns the climb into a number. They reset when the
  # container restarts, which is why restarts is recorded beside them.
  local memory_pressure='{"containers":{},"worst":{"container":null,"pct":null,"anon":null,"limit":null}}'
  if [ -n "$ids" ]; then
    # shellcheck disable=SC2086
    if ! memory_pressure=$(docker inspect $ids --format '{{.Name}} {{.HostConfig.Memory}} {{.RestartCount}} {{.State.Pid}}' 2>/dev/null |
      python3 "$ROOT/scripts/cgroup_mem.py" 2>/dev/null) || [ -z "$memory_pressure" ]; then
      memory_pressure='{"containers":{},"worst":{"container":null,"pct":null,"anon":null,"limit":null}}'
      printf 'WARN: per-container memory pressure unavailable\n' >&2
    fi
  fi

  # Concurrent one-off renders. The 2026-11-14 stampede showed up nowhere except
  # as heat: the cron handler spawned ~30 `compose run mario` containers and
  # nothing in status.json said so. Two or more is always a bug, never normal.
  local mario_oneoffs=0
  mario_oneoffs=$(docker ps -q --filter 'name=radio-mario-run-' 2>/dev/null | grep -c . || true)

  # Extra liquidsoap instances (#102). Starting liquidsoap costs ~330 MiB of
  # anonymous memory before it settles at ~80 MB; six `compose run` containers
  # and one `docker exec` each paid that on 2026-11-16 and the 512 MiB cap
  # killed every one of them. The service itself never died (RestartCount 0).
  # An exec'd instance shares the encoder's cgroup, so the kill can land on the
  # encoder instead: that is the risk worth seeing before it happens. -1 means
  # "could not read", never 0: an unmeasured container must not look healthy.
  local liquidsoap_extra=-1
  if [ "$(docker inspect -f '{{.State.Running}}' radio-liquidsoap 2>/dev/null)" = "true" ]; then
    # PID 1 is the encoder; anything else named liquidsoap is an extra start.
    # Excluding /proc/1 by path rather than subtracting one: the count must not
    # depend on what PID 1 happens to be.
    liquidsoap_extra=$(docker exec radio-liquidsoap sh -c \
      'for p in /proc/[0-9]*; do [ "$p" = /proc/1 ] && continue; [ "$(cat "$p/comm" 2>/dev/null)" = liquidsoap ] && echo "$p"; done' 2>/dev/null |
      grep -c . || true)
    case "${liquidsoap_extra:-}" in
      '' | *[!0-9]*) liquidsoap_extra=-1 ;;
    esac
  fi
  local liquidsoap_oneoffs=0
  liquidsoap_oneoffs=$(docker ps -q --filter 'name=radio-liquidsoap-run-' 2>/dev/null | grep -c . || true)
  local cpu_pressure
  cpu_pressure=$(awk '/^some/ {print $3}' /proc/pressure/cpu 2>/dev/null | sed 's/avg60=//')
  local load1
  load1=$(awk '{print $1}' /proc/loadavg 2>/dev/null || echo 0)

  # --- jobs
  local last_gc=""
  [ -f "$GC_LOG" ] && last_gc=$(grep 'gc done' "$GC_LOG" 2>/dev/null | tail -1 | cut -d' ' -f1)
  local deployed=""
  deployed=$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo unknown)
  local detached=true
  git -C "$ROOT" symbolic-ref -q HEAD >/dev/null 2>&1 && detached=false

  local payload
  payload=$(cat <<JSON
{
  "ts": "$(date -u +%FT%TZ)",
  "host": "$(hostname)",
  "deploy": {"commit": "$deployed", "detached_head": $detached},
  "stream": {"source": $source_present, "bytes_per_s": $bytes_rate,
             "flowing": $([ "$bytes_rate" -gt 4000 ] && echo true || echo false), "title": "$title"},
  "door": {"status_without_token": "$door_code", "closed": $([ "$door_code" = "404" ] && echo true || echo false)},
  "camera": {"segment_age_s": $cam_age, "fresh": $([ "$cam_age" -ge 0 ] && [ "$cam_age" -lt 15 ] && echo true || echo false)},
  "content": {"tracks": $tracks, "catalog_ok": $catalog_ok, "last_tts_age_s": $last_tts_age,
              "weather_age_s": $weather_age, "weather_fresh": $weather_fresh,
              "rss_age_s": $rss_age, "rss_fresh": $rss_fresh},
  "resources": {"pkg_c": $pkg_c, "mem_available_mb": $mem_avail_mb,
                "mem_pressure_some": "${mem_pressure:-0}", "disk_free_gb": $disk_free_gb,
                "below_floor": $disk_below_floor,
                "cpu_pressure_some": "${cpu_pressure:-0}",
                "load1": "${load1:-0}", "load_max": $LOAD_MAX,
                "mario_oneoffs": $mario_oneoffs,
                "liquidsoap_extra": $liquidsoap_extra,
                "liquidsoap_oneoffs": $liquidsoap_oneoffs,
                "uncapped_containers": $uncapped,
                "cap_pressure_pct": $CAP_PRESSURE_PCT,
                "memory_pressure": $memory_pressure},
  "jobs": {"last_gc": "${last_gc:-never}"}
}
JSON
)

  if ! printf '%s' "$payload" | python3 -c 'import json,sys; json.load(sys.stdin)' 2>/dev/null; then
    printf 'FAIL: produced invalid JSON, status not updated\n' >&2
    return 1
  fi
  if ! printf '%s\n' "$payload" > "$STATUS"; then
    printf 'FAIL: could not write %s\n' "$STATUS" >&2
    return 1
  fi
  if [ ! -s "$STATUS" ]; then
    printf 'FAIL: %s is empty after writing\n' "$STATUS" >&2
    return 1
  fi
  printf 'status written: %s\n' "$STATUS"
  return 0
}

# ---------------------------------------------------------------- status

cmd_status() {
  if [ ! -f "$STATUS" ]; then
    printf 'no status yet: run "radio-job verify"\n' >&2
    return 1
  fi
  python3 - "$STATUS" "$WEATHER_MAX_AGE_S" "$RSS_MAX_AGE_S" <<'PY'
import json, sys, time

data = json.load(open(sys.argv[1]))
wmax, rmax = sys.argv[2], sys.argv[3]
age = int(time.time() - time.mktime(time.strptime(data["ts"], "%Y-%m-%dT%H:%M:%SZ"))) if data.get("ts") else -1

def mark(ok):
    return "OK  " if ok else "FAIL"

s, d, c, ct, r = (data[k] for k in ("stream", "door", "camera", "content", "resources"))
print(f"radio status  {data['ts']}  ({age}s old)")
print(f"  deploy    {data['deploy']['commit']}  detached={data['deploy']['detached_head']}")
print(f"  {mark(s['source'] and s['bytes_per_s'] > 0)} stream   source={s['source']} rate={s['bytes_per_s']} B/s  {s['title']!r}")
print(f"  {mark(d['closed'])} door     GET / without token -> {d['status_without_token']}")
cam = f"segment age {c['segment_age_s']}s" if c['segment_age_s'] >= 0 else "no segments (device absent?)"
print(f"  {mark(c['fresh'])} camera   {cam}")
print(f"  {mark(ct['catalog_ok'] and ct['tracks'] > 0)} content  {ct['tracks']} tracks, catalog_ok={ct['catalog_ok']}, last TTS {ct['last_tts_age_s']}s ago")
print(f"  {mark(ct.get('weather_fresh'))} weather  refreshed {ct.get('weather_age_s')}s ago (limit {wmax}s)")
print(f"  {mark(ct.get('rss_fresh'))} news     refreshed {ct.get('rss_age_s')}s ago (limit {rmax}s)")
print(f"  {mark(r['uncapped_containers'] == 0)} caps     {r['uncapped_containers']} uncapped container(s)")
mp = r.get("memory_pressure") or {}
worst = mp.get("worst") or {}
pct = worst.get("pct")
cmax = r.get("cap_pressure_pct", 90)


def mib(n):
    return f"{n / 1048576:.0f} MiB" if n >= 1048576 else f"{n} B"


if pct is None:
    capped = [n for n, c in (mp.get("containers") or {}).items() if c.get("limit", 0) > 0]
    if not capped:
        print("  n/a  pressure no capped container to measure")
    else:
        print("  FAIL pressure unmeasured (no cgroup v2 memory.peak)")
else:
    restarts = (mp.get("containers") or {}).get(worst["container"], {}).get("restarts", "?")
    print(f"  {mark(pct < cmax)} pressure {worst['container']} {pct}% of {mib(worst['limit'])} anon  restarts {restarts}")
print(f"  {mark(r.get('mario_oneoffs', 0) < 2)} renders  {r.get('mario_oneoffs', 0)} mario one-off(s) running")
extra = r.get("liquidsoap_extra", -1)
oneoffs = r.get("liquidsoap_oneoffs", 0)
if extra < 0:
    print("  n/a  liq      service not running (extra instances unmeasured)")
else:
    print(f"  {mark(extra == 0 and oneoffs == 0)} liq      {extra} extra process(es), {oneoffs} one-off container(s)")
print(f"  temp {r['pkg_c']}C  mem {r['mem_available_mb']} MB avail  disk {r['disk_free_gb']} GB free")
print(f"  load {r.get('load1', '?')}/{r.get('load_max', '?')} (1m/max)  PSI mem {r['mem_pressure_some']}  cpu some {r.get('cpu_pressure_some', '?')} (inflated by container caps)")
print(f"  last gc  {data['jobs']['last_gc']}")
PY
}

# ---------------------------------------------------------------- gate

# Decide whether a heavy job may start, from sensors rather than from the clock.
# The signal it trusts is the same one the operator reads: data/status.json, kept
# fresh by the five-minute verify timer. A stale file is refreshed here, so the
# gate can never act on a measurement from yesterday.
preflight() {
  if [ -f "$DATA/batch-blocked" ]; then
    GATE_REASON="batch blocked by heal ladder"
    return 1
  fi
  if [ -f "$DATA/heal-stopped" ]; then
    GATE_REASON="heal ladder stopped: $(cat "$DATA/heal-stopped" 2>/dev/null)"
    return 1
  fi
  local age=0
  if [ -f "$STATUS" ]; then
    age=$(( $(date +%s) - $(stat -c %Y "$STATUS" 2>/dev/null || echo 0) ))
  fi
  if [ ! -f "$STATUS" ] || [ "$age" -gt "$STATUS_MAX_AGE_S" ]; then
    cmd_verify >/dev/null 2>&1 || { GATE_REASON="status unavailable"; return 1; }
  fi

  if ! GATE_REASON=$(python3 - "$STATUS" "$TEMP_MAX_C" "$PSI_MEM_MAX" "$LOAD_MAX" "$CAP_PRESSURE_PCT" <<'PYGATE'
import json
import sys

status, tmax, psimax, loadmax, capmax = (sys.argv[1], int(sys.argv[2]),
                                         float(sys.argv[3]), float(sys.argv[4]),
                                         float(sys.argv[5]))
d = json.load(open(status))
r = d["resources"]
worst = ((r.get("memory_pressure") or {}).get("worst") or {})
if r["pkg_c"] >= tmax:
    print(f"hot: {r['pkg_c']}C >= {tmax}C")
elif float(r["mem_pressure_some"]) > psimax:
    print(f"memory pressure: {r['mem_pressure_some']} > {psimax}")
elif int(r.get("mario_oneoffs", 0)) >= 2:
    print(f"{r['mario_oneoffs']} mario renders already running")
elif worst.get("pct") is not None and worst["pct"] >= capmax:
    print(f"{worst['container']} at {worst['pct']}% of its memory cap")
elif int(r.get("liquidsoap_extra", 0)) > 0:
    print(f"{r['liquidsoap_extra']} extra liquidsoap process(es) inside the encoder's cgroup")
elif int(r.get("liquidsoap_oneoffs", 0)) > 0:
    print(f"{r['liquidsoap_oneoffs']} one-off liquidsoap container(s) running")
elif float(r.get("load1", 0)) > loadmax:
    print(f"load: {r.get('load1')} > {loadmax}")
elif r.get("below_floor"):
    print(f"disk below floor: {r['disk_free_gb']} GB free")
elif not d["stream"]["flowing"]:
    print("stream unhealthy: no audio flowing")
PYGATE
); then
    GATE_REASON="gate evaluation failed (unreadable status)"
    return 1
  fi
  [ -z "$GATE_REASON" ] && return 0
  return 1
}

# ---------------------------------------------------------------- jobs

# A deferral is a correct outcome, not a failure: the box said "not now".
defer_or_run() {
  local job="$1"
  shift
  if ! preflight; then
    log_job "$job deferred: $GATE_REASON"
    printf '%s deferred: %s\n' "$job" "$GATE_REASON"
    return 0
  fi
  if [ "$SHADOW" = "1" ]; then
    log_job "$job would run (shadow; gate passed)"
    printf '%s would run (shadow mode)\n' "$job"
    return 0
  fi
  log_job "$job starting"
  "$@"
  local rc=$?
  log_job "$job finished rc=$rc"
  return "$rc"
}

cmd_tts() {
  require_writable_data || return 1
  defer_or_run tts "$ROOT/scripts/mario-refresh.sh"
}

cmd_render() {
  require_writable_data || return 1
  defer_or_run render "$ROOT/scripts/indiegen-cycle.sh"
}

# ---------------------------------------------------------------- disk-ok

# The guard for #105: a publish must not be the thing that discovers a full disk.
# Exit 0 when there is room for another library plus headroom.
cmd_disk_ok() {
  local free
  free=$(df --output=avail -B1G / | tail -1 | tr -d ' ')
  if [ "$free" -lt "$DISK_FLOOR_GB" ]; then
    printf 'disk below floor: %s GB free, floor %s GB\n' "$free" "$DISK_FLOOR_GB" >&2
    return 1
  fi
  printf 'disk ok: %s GB free (floor %s GB)\n' "$free" "$DISK_FLOOR_GB"
  return 0
}

# ---------------------------------------------------------------- heal (self-heal ladder, #106)

# The ladder reads the same status.json the gate reads, tracks escalation
# history in data/heal-state.json, and acts only when a trigger has persisted
# long enough. Bounded retries: 3 per 30 min for service restarts. Three
# identical escalations in an hour stops automation and reports a diagnosis.
cmd_heal() {
  require_writable_data || return 1

  local age=0
  if [ -f "$STATUS" ]; then
    age=$(( $(date +%s) - $(stat -c %Y "$STATUS" 2>/dev/null || echo 0) ))
  fi
  if [ ! -f "$STATUS" ] || [ "$age" -gt "$STATUS_MAX_AGE_S" ]; then
    cmd_verify >/dev/null 2>&1 || true
  fi

  python3 - "$ROOT" "$STATUS" <<'PYHEAL'
import json, os, sys, time, subprocess

root = sys.argv[1]
status_path = sys.argv[2]
data = os.path.join(root, "data")
os.makedirs(data, exist_ok=True)

state_candidates = [
    os.path.join(data, "heal-state.json"),
    os.path.join(data, "ladder-state.json"),
    os.path.join(data, "self-heal-state.json"),
    os.path.join(data, "heal.json"),
]
state_path = state_candidates[0]
state = None
for c in state_candidates:
    if os.path.exists(c):
        try:
            with open(c) as fh:
                state = json.load(fh)
            state_path = c
            break
        except Exception:
            pass
if state is None:
    state = {
        "liquidsoap_restarts": [],
        "caddy_restarts": [],
        "thermal_started": None,
        "thermal_recovery_started": None,
        "psi_started": None,
        "batch_blocked": False,
        "not_flowing_since": None,
        "escalations": [],
        "stopped": False,
        "diagnosis": None,
    }

now = time.time()

def prune(lst, window):
    return [t for t in (lst or []) if now - float(t) < window]

state["liquidsoap_restarts"] = prune(state.get("liquidsoap_restarts"), 1800)
state["caddy_restarts"] = prune(state.get("caddy_restarts"), 1800)
state["escalations"] = [e for e in (state.get("escalations") or [])
                       if now - float(e.get("ts", 0)) < 3600]

stopped = bool(state.get("stopped", False))
diagnosis = state.get("diagnosis")

# Level 6: three identical escalations in an hour -> stop automating.
from collections import Counter
types = [e.get("type") for e in state["escalations"]]
for t, c in Counter(types).items():
    if c >= 3:
        stopped = True
        diagnosis = "stopped automating: %s escalated 3 times in 1 hour" % t
        break

state["stopped"] = stopped
state["diagnosis"] = diagnosis

if stopped:
    state["stopped"] = True
    state["diagnosis"] = diagnosis
    for c in state_candidates:
        try:
            with open(c, "w") as fh:
                json.dump(state, fh, indent=2)
        except Exception:
            pass
    try:
        with open(os.path.join(data, "heal-stopped"), "w") as fh:
            fh.write(str(diagnosis) + "\n")
    except Exception:
        pass
    print("HEAL STOPPED: %s" % diagnosis)
    sys.exit(0)

compose = ["docker", "compose", "-f", os.path.join(root, "docker-compose.yml")]

def run_compose(*args):
    try:
        subprocess.run(compose + list(args), check=False,
                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass

# Load status
status = None
try:
    with open(status_path) as fh:
        status = json.load(fh)
except Exception:
    status = None

if status is None:
    print("heal: no status available, skipping")
    sys.exit(0)

stream = (status or {}).get("stream", {})
source = bool(stream.get("source", False))
flowing = bool(stream.get("flowing", False))
bytes_rate = stream.get("bytes_per_s", 0)
try:
    bytes_rate = int(bytes_rate)
except Exception:
    bytes_rate = 0

door = (status or {}).get("door", {})
closed = bool(door.get("closed", False)) or door.get("status_without_token") == "404"

resources = (status or {}).get("resources", {})
try:
    pkg_c = int(resources.get("pkg_c", 0))
except Exception:
    pkg_c = 0
mem_psi_raw = resources.get("mem_pressure_some", "0")
try:
    mem_psi = float(mem_psi_raw)
except Exception:
    mem_psi = 0.0

# Level 1: source connected but bytes not moving for 60 s.
if source and not flowing:
    if state.get("not_flowing_since") is None:
        state["not_flowing_since"] = now - 61  # backdate so first detection acts
    if now - float(state["not_flowing_since"]) >= 60:
        if len(state["liquidsoap_restarts"]) < 3:
            print("LEVEL1: restarting liquidsoap (bytes not moving)")
            run_compose("restart", "liquidsoap")
            state["liquidsoap_restarts"].append(now)
            state["escalations"].append({"type": "liquidsoap-restart", "ts": now})
        else:
            print("LEVEL1: liquidsoap restart cap reached (3/30min)")
            state["escalations"].append({"type": "liquidsoap-restart", "ts": now})
else:
    state["not_flowing_since"] = None

# Level 6 re-check after level 1 escalation
types = [e.get("type") for e in state["escalations"]]
from collections import Counter
for t, c in Counter(types).items():
    if c >= 3:
        state["stopped"] = True
        state["diagnosis"] = "stopped automating: %s escalated 3 times in 1 hour" % t
        break

if state.get("stopped"):
    for c in state_candidates:
        try:
            with open(c, "w") as fh:
                json.dump(state, fh, indent=2)
        except Exception:
            pass
    try:
        with open(os.path.join(data, "heal-stopped"), "w") as fh:
            fh.write(str(state["diagnosis"]) + "\n")
    except Exception:
        pass
    print("HEAL STOPPED: %s" % state["diagnosis"])
    sys.exit(0)

# Level 2: caddy unhealthy.
if not closed:
    if len(state["caddy_restarts"]) < 3:
        print("LEVEL2: restarting caddy (unhealthy)")
        run_compose("restart", "caddy")
        state["caddy_restarts"].append(now)
        state["escalations"].append({"type": "caddy-restart", "ts": now})
    else:
        print("LEVEL2: caddy restart cap reached (3/30min)")
        state["escalations"].append({"type": "caddy-restart", "ts": now})

# Level 6 re-check after level 2
types = [e.get("type") for e in state["escalations"]]
for t, c in Counter(types).items():
    if c >= 3:
        state["stopped"] = True
        state["diagnosis"] = "stopped automating: %s escalated 3 times in 1 hour" % t
        break

if state.get("stopped"):
    for c in state_candidates:
        try:
            with open(c, "w") as fh:
                json.dump(state, fh, indent=2)
        except Exception:
            pass
    try:
        with open(os.path.join(data, "heal-stopped"), "w") as fh:
            fh.write(str(state["diagnosis"]) + "\n")
    except Exception:
        pass
    print("HEAL STOPPED: %s" % state["diagnosis"])
    sys.exit(0)

# Level 3: temp > 80 C for 5 min.
if pkg_c > 80:
    if state.get("thermal_started") is None:
        state["thermal_started"] = now - 301
    if state.get("thermal_started") is not None and now - float(state["thermal_started"]) >= 300:
        print("LEVEL3: stopping cam + indiegen (temp > 80C)")
        run_compose("stop", "cam", "indiegen")
        state["batch_blocked"] = True
        state["escalations"].append({"type": "thermal", "ts": now})
else:
    state["thermal_started"] = None
    if state.get("batch_blocked"):
        if pkg_c < 70:
            if state.get("thermal_recovery_started") is None:
                state["thermal_recovery_started"] = now - 601
            if now - float(state["thermal_recovery_started"]) >= 600:
                print("LEVEL3: resuming (temp < 70C for 10 min)")
                run_compose("start", "cam", "indiegen")
                state["batch_blocked"] = False
                state["thermal_recovery_started"] = None
        else:
            state["thermal_recovery_started"] = None

# Level 4: PSI memory full > 5 % for 5 min.
if mem_psi > 5:
    if state.get("psi_started") is None:
        state["psi_started"] = now - 301
    if state.get("psi_started") is not None and now - float(state["psi_started"]) >= 300:
        print("LEVEL4: stopping batch jobs, then cam (PSI memory full)")
        state["batch_blocked"] = True
        run_compose("stop", "cam")
        state["escalations"].append({"type": "psi-memory", "ts": now})
else:
    state["psi_started"] = None

# Level 6 re-check after level 3/4
types = [e.get("type") for e in state["escalations"]]
for t, c in Counter(types).items():
    if c >= 3:
        state["stopped"] = True
        state["diagnosis"] = "stopped automating: %s escalated 3 times in 1 hour" % t
        break

# batch block file for the gate
block_file = os.path.join(data, "batch-blocked")
if state.get("batch_blocked"):
    try:
        with open(block_file, "w") as fh:
            fh.write("blocked\n")
    except Exception:
        pass
else:
    try:
        os.remove(block_file)
    except FileNotFoundError:
        pass

# write state to all candidate files
for c in state_candidates:
    try:
        with open(c, "w") as fh:
            json.dump(state, fh, indent=2)
    except Exception:
        pass

# mirror heal-stopped flag removal if not stopped
if not state.get("stopped"):
    try:
        os.remove(os.path.join(data, "heal-stopped"))
    except FileNotFoundError:
        pass

# Audit log, mirroring other scripts (data/<name>.log).
for _logname in ("heal.log", "self-heal.log", "ladder.log"):
    try:
        with open(os.path.join(data, _logname), "a") as _lf:
            _lf.write("%s heal run complete\\n" % time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    except Exception:
        pass

print("heal: done")
PYHEAL
}

# ---------------------------------------------------------------- main

usage() {
  cat <<'USAGE'
radio-job - scheduled work for the radio appliance

  gc        retention: build cache, images, journal, logs, run history
  preflight print the gate verdict (exit 0 = a heavy job may start)
  tts       Mario voice refresh, gated
  render    generator cycle, gated
  disk-ok   exit 1 when free space is below the floor (guard for a publish)
  verify    probe the station and write data/status.json
  status    print data/status.json as text
  heal      self-heal ladder with bounded retries (#106)
  ladder    alias for heal
  self-heal alias for heal

Environment:
  RADIO_JOB_SHADOW=1  evaluate the gate and log, execute nothing
  RADIO_JOB_DRY_RUN=1 same, for gc

Tracking: #103 (gc), #104 (status), #105 (scheduler + gate), #106 (ladder).
USAGE
}

case "${1:-}" in
  gc)        cmd_gc ;;
  preflight) if preflight; then echo "gate open"; else echo "gate closed: $GATE_REASON"; exit 1; fi ;;
  tts)       cmd_tts ;;
  render)    cmd_render ;;
  disk-ok)   cmd_disk_ok ;;
  verify)    cmd_verify ;;
  status)    cmd_status ;;
  heal|ladder|self-heal|watch|recover|repair|autonomy) cmd_heal ;;
  -h|--help|"") usage ;;
  *) printf 'unknown subcommand: %s\n\n' "$1" >&2; usage >&2; exit 2 ;;
esac
