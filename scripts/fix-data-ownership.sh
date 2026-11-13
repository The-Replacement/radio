#!/usr/bin/env bash
# Fix ownership of bind-mounted runtime data dirs for radio services (#83).
# Intended to be run as root (or via scoped passwordless sudo) on deploy hosts.
# Safe/idempotent: only chowns known data paths under this repo.
set -euo pipefail

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

fix=0
chown_path() {
  local path="$1" owner="$2" label="$3"
  if [[ -e "$path" ]]; then
    chown -R "$owner" "$path"
    echo "fixed $label -> $owner ($path)"
    fix=$((fix+1))
  else
    echo "skip  $label (missing: $path)"
  fi
}

echo "radio: fixing bind-mount ownership under $ROOT"
chown_path "$ROOT/data/indiegen" "${INDIEGEN_UID}:${INDIEGEN_GID}" "indiegen"
chown_path "$ROOT/data/host"     "${MARIO_UID}:${MARIO_GID}"       "mario/host"
chown_path "$ROOT/data/cam"      "${CAM_UID}:${CAM_GID}"           "cam"
chown_path "$ROOT/data/caddy"    "${CADDY_UID}:${CADDY_GID}"       "caddy"

# Do not touch secrets/tokens.txt or caddy/tokens.caddy here; they have their
# own permission constraints (#87).
echo "radio: ownership fix complete ($fix path group(s) processed)"
