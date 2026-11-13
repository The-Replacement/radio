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

# RADIO_JOB_DRY_RUN=1 prints what gc would do and touches nothing. The first run
# of a destructive script should not be its first test.
DRY_RUN="${RADIO_JOB_DRY_RUN:-0}"

# Retention bounds. One place, so a policy change is not a code change.
BUILD_CACHE_KEEP="5GB"
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

human() { numfmt --to=iec --suffix=B "$1" 2>/dev/null || printf '%sB' "$1"; }

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
    local cache
    cache=$(docker system df --format '{{.Type}} {{.Size}}' 2>/dev/null | awk '$1=="Build"{print $2}')
    if docker builder prune -f --keep-storage "$BUILD_CACHE_KEEP" >> "$GC_LOG" 2>&1; then
      log_gc "build cache pruned (was ${cache:-unknown}), keeping $BUILD_CACHE_KEEP"
    else
      log_gc "FAIL: builder prune failed"
    fi
    if [ "$DRY_RUN" = "1" ]; then
      log_gc "dry: would prune dangling images"
    else
      local imgs
      imgs=$(docker image prune -f 2>&1 | tail -1)
      log_gc "dangling images: ${imgs:-no output}"
    fi
  else
    log_gc "SKIP: docker daemon not reachable"
  fi

  # Journal: needs root. Absent passwordless sudo this is a line in the log, not
  # a failed run.
  if [ "$DRY_RUN" = "1" ]; then
    log_gc "dry: would vacuum journal to $JOURNAL_MAX"
  elif sudo -n journalctl --vacuum-size="$JOURNAL_MAX" >/dev/null 2>&1; then
    log_gc "journal vacuumed to $JOURNAL_MAX"
  else
    log_gc "SKIP: journal vacuum needs root (SystemMaxUse in journald.conf is the durable fix)"
  fi

  rotate_if_large "$DATA/deploy.log" "$DEPLOY_LOG_MAX_BYTES" 2000
  rotate_if_large "$DATA/mario-refresh.log" $((1024 * 1024)) 500

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
  door_code=$(curl -s -o /dev/null -m 5 -w '%{http_code}' http://127.0.0.1:8090/ 2>/dev/null || echo 000)

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

  # --- resources
  local pkg_c=0 mem_avail_mb disk_free_gb mem_pressure disk_below_floor
  local tz
  tz=$(for z in /sys/class/thermal/thermal_zone*/temp; do cat "$z" 2>/dev/null; done | sort -n | tail -1)
  [ -n "${tz:-}" ] && pkg_c=$((tz / 1000))
  mem_avail_mb=$(awk '/^MemAvailable:/ {printf "%d", $2/1024}' /proc/meminfo)
  disk_free_gb=$(df --output=avail -B1G / | tail -1 | tr -d ' ')
  disk_below_floor=$([ "$disk_free_gb" -lt "$DISK_FLOOR_GB" ] && echo true || echo false)
  mem_pressure=$(awk '/^some/ {print $2}' /proc/pressure/memory 2>/dev/null | sed 's/avg60=//')

  # --- caps: every radio container must report a memory limit
  local uncapped=0
  local ids
  ids=$(docker ps -q --filter 'name=radio-' 2>/dev/null)
  if [ -n "$ids" ]; then
    # shellcheck disable=SC2086
    uncapped=$(docker inspect $ids --format '{{if eq .HostConfig.Memory 0}}{{.Name}}{{end}}' 2>/dev/null | grep -c . )
  fi

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
  "content": {"tracks": $tracks, "catalog_ok": $catalog_ok, "last_tts_age_s": $last_tts_age},
  "resources": {"pkg_c": $pkg_c, "mem_available_mb": $mem_avail_mb,
                "mem_pressure_some": "${mem_pressure:-0}", "disk_free_gb": $disk_free_gb,
                "below_floor": $disk_below_floor,
                "uncapped_containers": $uncapped},
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
  python3 - "$STATUS" <<'PY'
import json, sys, time

data = json.load(open(sys.argv[1]))
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
print(f"  {mark(r['uncapped_containers'] == 0)} caps     {r['uncapped_containers']} uncapped container(s)")
print(f"  temp {r['pkg_c']}C  mem {r['mem_available_mb']} MB avail  disk {r['disk_free_gb']} GB free  PSI {r['mem_pressure_some']}")
print(f"  last gc  {data['jobs']['last_gc']}")
PY
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

# ---------------------------------------------------------------- main

usage() {
  cat <<'USAGE'
radio-job - scheduled work for the radio appliance

  gc        retention: build cache, images, journal, logs, run history
  disk-ok   exit 1 when free space is below the floor (guard for a publish)
  verify    probe the station and write data/status.json
  status    print data/status.json as text

Planned with #105 (scheduler + resource gate): render, tts, rotate.
USAGE
}

case "${1:-}" in
  gc)      cmd_gc ;;
  disk-ok) cmd_disk_ok ;;
  verify)  cmd_verify ;;
  status)  cmd_status ;;
  -h|--help|"") usage ;;
  *) printf 'unknown subcommand: %s\n\n' "$1" >&2; usage >&2; exit 2 ;;
esac
