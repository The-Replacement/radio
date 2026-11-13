#!/usr/bin/env bash
# Fix ownership of bind-mounted runtime data dirs for radio services (#83).
# Safe/idempotent: only touches known data paths under this repo.
#
# Strategy:
# 1) Try direct chown (works if already owned/writable by current user, or root).
# 2) If partial/failed and docker is available, try a disposable root container
#    bind-mounting the same paths (works for users in the docker group; docker
#    group is effectively privileged over the host).
# 3) If still unresolved, exit non-zero with guidance (scoped sudoers wiring).
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$ROOT/.env"

get_env() {
  local key="$1" default="$2"
  local val=""
  if [[ -f "$ENV_FILE" ]]; then
    val="$(grep -E "^${key}=" "$ENV_FILE" 2>/dev/null | tail -n1 | cut -d= -f2- || true)"
  fi
  printf '%s' "${val:-$default}"
}

INDIEGEN_UID="$(get_env INDIEGEN_UID 1000)"
INDIEGEN_GID="$(get_env INDIEGEN_GID 1000)"
MARIO_UID="$(get_env MARIO_UID "$INDIEGEN_UID")"
MARIO_GID="$(get_env MARIO_GID "$INDIEGEN_GID")"
CAM_UID="$(get_env CAM_UID "$INDIEGEN_UID")"
CAM_GID="$(get_env CAM_GID "$INDIEGEN_GID")"
CADDY_UID="$(get_env CADDY_UID "$INDIEGEN_UID")"
CADDY_GID="$(get_env CADDY_GID "$INDIEGEN_GID")"

have_docker() {
  command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1
}

try_docker_fix() {
  # Run as root in a throwaway container to chown bind-mounted paths.
  local image="${FIX_OWNER_IMAGE:-alpine:3.21}"
  docker run --rm \
    -v "$ROOT/data/indiegen:/t/indiegen" \
    -v "$ROOT/data/host:/t/host" \
    -v "$ROOT/data/cam:/t/cam" \
    -v "$ROOT/data/caddy:/t/caddy" \
    "$image" sh -c "
      chown -R ${INDIEGEN_UID}:${INDIEGEN_GID} /t/indiegen 2>/dev/null || true
      chown -R ${MARIO_UID}:${MARIO_GID} /t/host 2>/dev/null || true
      chown -R ${CAM_UID}:${CAM_GID} /t/cam 2>/dev/null || true
      chown -R ${CADDY_UID}:${CADDY_GID} /t/caddy 2>/dev/null || true
    " >/dev/null 2>&1
}

fixed=0
failed=0

check_path() {
  local path="$1" uid="$2" gid="$3"
  [[ -e "$path" ]] || return 0
  # ok if no files/dirs with mismatched owner/group
  ! find "$path" \( -not -user "$uid" -o -not -group "$gid" \) -print -quit 2>/dev/null | grep -q .
}

chown_path() {
  local path="$1" owner="$2" label="$3" uid="$4" gid="$5"
  if [[ ! -e "$path" ]]; then
    echo "skip  $label (missing: $path)"
    return 0
  fi
  if check_path "$path" "$uid" "$gid"; then
    echo "ok    $label -> $owner ($path)"
    fixed=$((fixed+1))
    return 0
  fi
  if chown -R "$owner" "$path" 2>/dev/null && check_path "$path" "$uid" "$gid"; then
    echo "fixed $label -> $owner ($path)"
    fixed=$((fixed+1))
    return 0
  fi
  echo "warn  $label -> $owner ($path) direct chown insufficient"
  return 1
}

echo "radio: fixing bind-mount ownership under $ROOT"
need_docker=0
chown_path "$ROOT/data/indiegen" "${INDIEGEN_UID}:${INDIEGEN_GID}" "indiegen"   "$INDIEGEN_UID" "$INDIEGEN_GID" || need_docker=1
chown_path "$ROOT/data/host"     "${MARIO_UID}:${MARIO_GID}"       "mario/host" "$MARIO_UID"    "$MARIO_GID"    || need_docker=1
chown_path "$ROOT/data/cam"      "${CAM_UID}:${CAM_GID}"           "cam"        "$CAM_UID"      "$CAM_GID"      || need_docker=1
chown_path "$ROOT/data/caddy"    "${CADDY_UID}:${CADDY_GID}"       "caddy"      "$CADDY_UID"    "$CADDY_GID"    || need_docker=1

if [[ "$need_docker" -eq 1 ]]; then
  if have_docker; then
    echo "info  retrying via docker (disposable root container)"
    try_docker_fix || true
    # re-check
    fixed=0; failed=0
    check_path "$ROOT/data/indiegen" "$INDIEGEN_UID" "$INDIEGEN_GID" && fixed=$((fixed+1)) || failed=$((failed+1))
    check_path "$ROOT/data/host"     "$MARIO_UID"    "$MARIO_GID"    && fixed=$((fixed+1)) || failed=$((failed+1))
    check_path "$ROOT/data/cam"      "$CAM_UID"      "$CAM_GID"      && fixed=$((fixed+1)) || failed=$((failed+1))
    check_path "$ROOT/data/caddy"    "$CADDY_UID"    "$CADDY_GID"    && fixed=$((fixed+1)) || failed=$((failed+1))
  else
    echo "warn  docker unavailable; cannot self-heal privileged ownership mismatches"
    failed=4
  fi
fi

# Do not touch secrets/tokens.txt or caddy/tokens.caddy here; they have their
# own permission constraints (#87).
echo "radio: ownership fix complete (ok=$fixed warn=$failed)"
if [[ "$failed" -gt 0 ]]; then
  echo "hint: run with privileges or wire scoped sudoers (docs/HARDENING.md)" >&2
  exit 2
fi
