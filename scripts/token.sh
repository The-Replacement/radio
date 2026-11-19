#!/usr/bin/env bash
# token.sh - manages who can listen to the radio.
#
#   ./scripts/token.sh add <name> [expiry]   grants access (does not print links)
#   ./scripts/token.sh reveal <name>         prints invitation/camera links (sensitive)
#   ./scripts/token.sh rm  <name>            cuts their access
#   ./scripts/token.sh ls                    lists who has access
#   ./scripts/token.sh regen                 regenerates Caddy's matcher
#   ./scripts/token.sh rotate <name> [expiry] reissues token/cookie (revokes old)
#   ./scripts/token.sh prune                 removes expired credentials from storage
#   ./scripts/token.sh check                 validates production cookie/transport config
#
# The expiry is optional and takes two forms:
#
#   ./scripts/token.sh add juan 30d          expires in 30 days
#   ./scripts/token.sh add juan 2026-12-31   expires on that date (inclusive)
#
# With no expiry, the credential lasts forever.
#
# Each person receives TWO credentials:
#
#   token    - travels once, in the invitation link (?t=...). The browser
#              exchanges it for the cookie and drops it. It stays available
#              for players (VLC, mpv, ffmpeg), which do not handle cookies.
#
#   cookieid - what the browser sends on every request after the exchange.
#              Thanks to it the token does not appear in the URL nor in
#              Caddy's logs on every playback.
#
# Both are revoked together with `rm`.
#
# The expiry is applied in Caddy, comparing against the current date: nothing
# has to be run when a credential expires.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TOKENS_FILE="$ROOT/secrets/tokens.txt"
CADDY_FILE="$ROOT/caddy/tokens.caddy"
GENERATOR="$ROOT/scripts/token-matcher.py"
COMPOSE=(docker compose -f "$ROOT/docker-compose.yml")

ensure_secret_permissions() {
  mkdir -p "$ROOT/secrets"
  chmod 700 "$ROOT/secrets" 2>/dev/null || true
  touch "$TOKENS_FILE"
  chmod 600 "$TOKENS_FILE" 2>/dev/null || true
  if [[ -f "$CADDY_FILE" ]]; then
    chmod 600 "$CADDY_FILE" 2>/dev/null || true
  fi
}

ensure_secret_permissions

random_val() {
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex 32
  else
    head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n'
  fi
}

# Normalises the expiry: "" | "30d" | "2026-12-31" -> "" | "YYYY-MM-DD".
# Exits with an error if the date is invalid or already past.
normalize_expiry() {
  python3 - "$1" <<'PY'
import re, sys
from datetime import date, timedelta

arg = sys.argv[1].strip()
if not arg:
    sys.exit(0)

m = re.fullmatch(r'(\d+)d', arg)
if m:
    print((date.today() + timedelta(days=int(m.group(1)))).isoformat())
    sys.exit(0)

try:
    d = date.fromisoformat(arg)
except ValueError:
    sys.exit(2)

if d < date.today():
    sys.exit(3)

print(d.isoformat())
PY
}

# Validates the config with Caddy itself before reloading.
# A broken matcher must not be able to take the radio down: if this fails,
# regenerate_matcher restores the previous file.
validate_caddy() {
  if "${COMPOSE[@]}" ps --status running --services 2>/dev/null | grep -qx caddy; then
    "${COMPOSE[@]}" exec -T caddy \
      caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null 2>&1
  else
    docker run --rm -v "$ROOT/caddy:/etc/caddy:ro" caddy:2-alpine@sha256:003a97808e781aa9f7e75cc2f43d34d461099031faf5e20faefb9f61126836f0 \
      caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null 2>&1
  fi
}

# Regenerates caddy/tokens.caddy from secrets/tokens.txt.
# The generator also migrates the old format and skips expired ones.
#
# TWO DELICATE THINGS, both have already bitten us:
#
# 1) Do NOT use `mv` to replace the file. Docker bind-mounts tokens.caddy by
#    INODE: if the replacement changes the inode, the container keeps seeing the
#    old version and `caddy reload` reloads garbage with no effect. You have to
#    write IN PLACE (`cat ... > file`), which truncates and reuses the inode.
#
# 2) Validate before accepting the config. A matcher that does not compile in
#    CEL leaves Caddy in a restart loop: the whole radio goes down.
regenerate_matcher() {
  local new_file="$CADDY_FILE.new_file"
  local backup
  backup="$(mktemp)"

  if ! python3 "$GENERATOR" "$TOKENS_FILE" "$new_file"; then
    echo "ERROR: could not generate the matcher; the current config was not touched." >&2
    rm -f "$new_file"
    return 1
  fi

  [[ -f "$CADDY_FILE" ]] && cat "$CADDY_FILE" > "$backup"

  # In-place write: keeps the inode the container has mounted.
  cat "$new_file" > "$CADDY_FILE"
  chmod 600 "$CADDY_FILE" 2>/dev/null || true
  rm -f "$new_file"

  if ! validate_caddy; then
    echo "ERROR: Caddy does not accept the generated config. Restoring the previous one." >&2
    cat "$backup" > "$CADDY_FILE"
    rm -f "$backup"
    echo "See the detail with:" >&2
    echo "  docker run --rm -v \"$ROOT/caddy:/etc/caddy:ro\" caddy:2-alpine@sha256:003a97808e781aa9f7e75cc2f43d34d461099031faf5e20faefb9f61126836f0 caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile" >&2
    return 1
  fi

  rm -f "$backup"
  return 0
}

reload_caddy() {
  if docker compose -f "$ROOT/docker-compose.yml" ps --status running --services 2>/dev/null | grep -qx caddy; then
    echo "-> reloading Caddy (without cutting the listeners)..."
    if "${COMPOSE[@]}" exec -T caddy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null 2>&1; then
      echo "-> done."
    else
      echo "-> could not reload hot; restarting the container." >&2
      "${COMPOSE[@]}" restart caddy >/dev/null
      echo "-> done."
    fi
  else
    echo "-> Caddy is not running; the change applies when it starts."
  fi
}

# PUBLIC_URL from .env, with a default. It is the only thing the two functions
# below share: each URL's format is different (token in the query vs. token in
# the path), so only the reading is extracted, not the shape.
public_url() {
  local public
  public="$(grep -E '^PUBLIC_URL=' "$ROOT/.env" 2>/dev/null | cut -d= -f2- || true)"
  public="${public:-http://localhost:8090}"
  printf '%s' "${public%/}"
}

url_for() {
  printf '%s/?t=%s\n' "$(public_url)" "$1"
}

# The camera URL for players (VLC, mpv, ffmpeg).
#
# The token goes in the PATH, not in the query, and it is not a whim: the HLS
# playlist points at the segments with relative paths, and the player requests
# them without the query. With ?t=... the playlist downloads and the segments
# give 404. With the token in a path segment, every relative request inherits it
# on its own.
cam_url_for() {
  printf '%s/cam/%s/index.m3u8\n' "$(public_url)" "$1"
}

# The name is used inside ERE expressions (^name ) and as a work file.
# Restricting it to a charset with no metacharacters closes the regex injection
# surface: with no ., *, +, (, ), [, ] in the name, the pattern `^${name} `
# matches only that person.
validate_name() {
  if ! [[ "$1" =~ ^[A-Za-z0-9_-]+$ ]]; then
    echo "Invalid name: '$1'. Use only letters, numbers, hyphen or underscore." >&2
    exit 1
  fi
}

# Consistency between the site and the cookie. Production over HTTPS must set
# the Secure flag or the browser may send the session cookie over plain HTTP.
# Fail fast on unsafe production profiles (audit O4/#89). Set
# ALLOW_INSECURE_COOKIE=1 only for intentional non-production exceptions.
validate_environment() {
  local site public_value flags
  site="$(grep -E '^SITE_ADDRESS=' "$ROOT/.env" 2>/dev/null | cut -d= -f2- || true)"
  public_value="$(grep -E '^PUBLIC_URL=' "$ROOT/.env" 2>/dev/null | cut -d= -f2- || true)"
  flags="$(grep -E '^COOKIE_FLAGS=' "$ROOT/.env" 2>/dev/null | cut -d= -f2- || true)"
  if [[ ( "$site" == https://* || "$public_value" == https://* ) && "$flags" != *Secure* ]]; then
    echo "ERROR: HTTPS is configured (SITE_ADDRESS/PUBLIC_URL) but COOKIE_FLAGS has no 'Secure'." >&2
    echo "       Add '; Secure' to COOKIE_FLAGS for production." >&2
    echo "       For an intentional exception (not recommended), rerun with ALLOW_INSECURE_COOKIE=1." >&2
    if [[ "${ALLOW_INSECURE_COOKIE:-0}" != "1" ]]; then
      exit 1
    fi
  fi
}

# Prints invitation URLs for a person. Full credentials are sensitive (audit
# O2/#87): reveal only on explicit request.
cmd_reveal() {
  local name="${1:?usage: token.sh reveal <name>}"
  validate_name "$name"
  if ! grep -qE "^${name} " "$TOKENS_FILE"; then
    echo "\"$name\" was not on the list." >&2
    exit 1
  fi
  local t v
  t="$(awk -v n="$name" '$1==n {print $2}' "$TOKENS_FILE")"
  v="$(awk -v n="$name" '$1==n {print $4}' "$TOKENS_FILE")"
  echo "Credential for \"$name\" (sensitive - do not share publicly/log):"
  [[ -n "$v" ]] && echo "  expires $v"
  echo
  echo "  Invitation (browser): $(url_for "$t")"
  echo "  Camera (VLC/mpv):    $(cam_url_for "$t")"
}

cmd_add() {
  local name="${1:?usage: token.sh add <name> [expiry]}"
  local requested="${2:-}"
  local expiry

  validate_name "$name"
  validate_environment

  # CAREFUL: after `if ! cmd`, $? is the negation's (always 0).
  # The code has to be saved before negating it.
  local rc=0
  expiry="$(normalize_expiry "$requested")" || rc=$?
  if [ "$rc" -ne 0 ]; then
    case "$rc" in
      3) echo "That date has already passed: '$requested'" >&2 ;;
      *) echo "Invalid expiry: '$requested'. Use 30d or 2026-12-31" >&2 ;;
    esac
    exit 1
  fi

  if grep -qE "^${name} " "$TOKENS_FILE"; then
    local v
    v="$(awk -v n="$name" '$1==n {print $4}' "$TOKENS_FILE")"
    echo "\"$name\" already has access." >&2
    [[ -n "$v" ]] && echo "(expires $v)" >&2
    echo "Credential links are sensitive and are not printed by default." >&2
    echo "Run: ./scripts/token.sh reveal \"$name\"" >&2
    return 0
  fi

  local t
  t="$(random_val)"
  printf '%s %s %s%s\n' "$name" "$t" "$(random_val)" "${expiry:+ $expiry}" >> "$TOKENS_FILE"
  chmod 600 "$TOKENS_FILE" 2>/dev/null || true
  regenerate_matcher
  echo "OK access granted to \"$name\""
  if [[ -n "$expiry" ]]; then
    echo "   expires $expiry (that day included)"
  else
    echo "   no expiry set (long-lived). Prefer an expiry, e.g. ./scripts/token.sh add \"$name\" 30d" >&2
  fi
  echo
  echo "  Credential links are sensitive (bearer tokens) and are not printed by default."
  echo "  To display the invitation and camera URLs now, run:"
  echo "    ./scripts/token.sh reveal \"$name\""
  echo
  echo "  Treat revealed links as secrets: avoid logs, shell history, screenshots, and unencrypted chats."
  echo "  Revoke when no longer needed: ./scripts/token.sh rm \"$name\""
  echo
  reload_caddy
}

cmd_rm() {
  local name="${1:?usage: token.sh rm <name>}"
  validate_name "$name"
  if ! grep -qE "^${name} " "$TOKENS_FILE"; then
    echo "\"$name\" was not on the list." >&2
    exit 1
  fi
  # The `|| true` is not decorative: if it is the LAST token, grep -v returns no
  # lines, exits with 1, and with `set -e` the script died here without warning.
  grep -vE "^${name} " "$TOKENS_FILE" > "$TOKENS_FILE.tmp" || true
  mv "$TOKENS_FILE.tmp" "$TOKENS_FILE"
  chmod 600 "$TOKENS_FILE" 2>/dev/null || true
  regenerate_matcher
  echo "OK access revoked for \"$name\" (token and cookie)"
  reload_caddy
}

cmd_ls() {
  if ! [[ -s "$TOKENS_FILE" ]]; then
    echo "(nobody has access yet)"
    return 0
  fi
  local today; today="$(date +%F)"
  printf '%-20s %-16s %s\n' "WHO" "TOKEN" "EXPIRES"
  while read -r n t _ v; do
    [[ -z "${n:-}" ]] && continue
    if [[ -z "${v:-}" ]]; then
      printf '%-20s %-16s %s\n' "$n" "****${t: -4}" "never"
    elif [[ "$v" < "$today" ]]; then
      printf '%-20s %-16s %s  <-- EXPIRED\n' "$n" "****${t: -4}" "$v"
    else
      printf '%-20s %-16s %s\n' "$n" "****${t: -4}" "$v"
    fi
  done < "$TOKENS_FILE"
}

cmd_regen() {
  validate_environment
  regenerate_matcher
  echo "OK caddy/tokens.caddy regenerated from secrets/tokens.txt"
  reload_caddy
}

cmd_rotate() {
  local name="${1:?usage: token.sh rotate <name> [expiry]}"
  local requested="${2:-}"
  local new_expiry current_expiry

  validate_name "$name"
  validate_environment

  if ! grep -qE "^${name} " "$TOKENS_FILE"; then
    echo "\"$name\" was not on the list." >&2
    exit 1
  fi

  current_expiry="$(awk -v n="$name" '$1==n {print $4}' "$TOKENS_FILE")"
  if [[ -n "$requested" ]]; then
    local rc=0
    new_expiry="$(normalize_expiry "$requested")" || rc=$?
    if [ "$rc" -ne 0 ]; then
      case "$rc" in
        3) echo "That date has already passed: '$requested'" >&2 ;;
        *) echo "Invalid expiry: '$requested'. Use 30d or 2026-12-31" >&2 ;;
      esac
      exit 1
    fi
  else
    new_expiry="$current_expiry"
  fi

  local tmp="$TOKENS_FILE.tmp.$$"
  awk -v n="$name" -v t="$(random_val)" -v c="$(random_val)" -v v="$new_expiry" '
    $1==n { printf "%s %s %s%s\n", n, t, c, (v=="" ? "" : " " v); next }
    { print }
  ' "$TOKENS_FILE" > "$tmp"
  mv "$tmp" "$TOKENS_FILE"
  chmod 600 "$TOKENS_FILE" 2>/dev/null || true
  regenerate_matcher
  echo "OK credentials rotated for \"$name\" (old token/cookie revoked)"
  if [[ -n "$new_expiry" ]]; then
    echo "   expires $new_expiry"
  else
    echo "   no expiry set (long-lived). Prefer an expiry, e.g. ./scripts/token.sh rotate \"$name\" 30d" >&2
  fi
  echo "   Reveal new links (sensitive): ./scripts/token.sh reveal \"$name\""
  reload_caddy
}

cmd_prune() {
  # Removes expired credentials from secrets/tokens.txt (lifecycle hygiene).
  # Active/non-expiring credentials are kept. Regenerates matcher afterward.
  if ! [[ -s "$TOKENS_FILE" ]]; then
    echo "(no credentials file or empty)"
    return 0
  fi
  local today tmp removed
  today="$(date +%F)"
  tmp="$TOKENS_FILE.tmp.$$"
  removed=$(awk -v today="$today" '
    NF>=4 && $4 < today { r++; next }
    { print }
    END { print r+0 > "/dev/stderr" }
  ' "$TOKENS_FILE" 2> "$tmp.count" > "$tmp" || true)
  removed="$(cat "$tmp.count" 2>/dev/null || echo 0)"
  rm -f "$tmp.count"
  if [[ "$removed" == "0" ]]; then
    rm -f "$tmp"
    echo "No expired credentials to prune."
    return 0
  fi
  mv "$tmp" "$TOKENS_FILE"
  chmod 600 "$TOKENS_FILE" 2>/dev/null || true
  regenerate_matcher
  echo "OK pruned $removed expired credential(s) from secrets/tokens.txt"
  reload_caddy
}

cmd_check() {
  local fatal=0
  local warn=0

  echo "Security/lifecycle check (#87):"

  if validate_environment; then
    echo "  PASS cookie/transport config basics"
  else
    echo "  FAIL cookie/transport config basics"
    fatal=1
  fi

  # File and directory permissions (bearer-equivalent artifacts)
  local dir_mode tok_mode caddy_mode
  dir_mode="$(stat -c '%a' "$ROOT/secrets" 2>/dev/null || echo missing)"
  tok_mode="$(stat -c '%a' "$TOKENS_FILE" 2>/dev/null || echo missing)"
  caddy_mode="$(stat -c '%a' "$CADDY_FILE" 2>/dev/null || echo missing)"

  if [[ "$dir_mode" == "700" ]]; then
    echo "  PASS secrets/ permissions (700)"
  else
    echo "  WARN secrets/ permissions are $dir_mode (expected 700); run token.sh to enforce or: chmod 700 secrets"
    warn=1
  fi
  if [[ "$tok_mode" == "600" ]]; then
    echo "  PASS secrets/tokens.txt permissions (600)"
  elif [[ "$tok_mode" == "missing" ]]; then
    echo "  WARN secrets/tokens.txt missing (no credentials stored)"
    warn=1
  else
    echo "  WARN secrets/tokens.txt permissions are $tok_mode (expected 600); run: chmod 600 secrets/tokens.txt"
    warn=1
  fi
  if [[ "$caddy_mode" == "600" ]]; then
    echo "  PASS caddy/tokens.caddy permissions (600)"
  elif [[ "$caddy_mode" == "missing" ]]; then
    echo "  WARN caddy/tokens.caddy missing; run: ./scripts/token.sh regen"
    warn=1
  else
    echo "  WARN caddy/tokens.caddy permissions are $caddy_mode (expected 600); run: chmod 600 caddy/tokens.caddy"
    warn=1
  fi

  # Lifecycle posture from stored credentials
  local today expired_in_storage non_expiring
  today="$(date +%F)"
  expired_in_storage=0
  non_expiring=0
  if [[ -s "$TOKENS_FILE" ]]; then
    expired_in_storage="$(awk -v today="$today" 'NF>=4 && $4 < today {c++} END {print c+0}' "$TOKENS_FILE")"
    non_expiring="$(awk 'NF>=2 && (NF < 4 || $4 == "") {c++} END {print c+0}' "$TOKENS_FILE")"
  fi

  if [[ "$expired_in_storage" -gt 0 ]]; then
    echo "  WARN $expired_in_storage expired credential(s) still in secrets/tokens.txt (omitted from matcher, but stored); run: ./scripts/token.sh prune"
    warn=1
  else
    echo "  PASS no expired credentials stored in secrets/tokens.txt"
  fi

  if [[ "$non_expiring" -gt 0 ]]; then
    echo "  WARN $non_expiring active credential(s) have no expiry (long-lived); prefer expiry and rotation (e.g. ./scripts/token.sh rotate <name> 30d)"
    warn=1
  else
    echo "  PASS all stored credentials have expiry dates"
  fi

  echo "  INFO secrets/tokens.txt and caddy/tokens.caddy are bearer-equivalent; restrict host access/backups and avoid shell history, logs, screenshots, and unencrypted sharing. Use 'reveal' only when needed."

  if [[ "$fatal" -ne 0 ]]; then
    echo "FAIL security/lifecycle check found fatal issues." >&2
    return 1
  fi
  if [[ "$warn" -ne 0 ]]; then
    echo "WARN security/lifecycle check passed fatal checks but found risks requiring attention." >&2
    return 0
  fi
  echo "OK security/lifecycle checks passed."
}

case "${1:-}" in
  add) shift; cmd_add "$@" ;;
  reveal|show) shift; cmd_reveal "$@" ;;
  rm|del|remove) shift; cmd_rm "$@" ;;
  regen|sync) cmd_regen ;;
  rotate) shift; cmd_rotate "$@" ;;
  prune) cmd_prune ;;
  check|doctor) cmd_check ;;
  ls|list) cmd_ls ;;
  *) sed -n '2,/^set -euo/p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' | sed '$d'; exit 1 ;;
esac
