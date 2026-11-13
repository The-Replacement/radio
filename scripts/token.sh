#!/usr/bin/env bash
# token.sh - manages who can listen to the radio.
#
#   ./scripts/token.sh add <name> [expiry]   grants access (does not print links)
#   ./scripts/token.sh reveal <name>         prints invitation/camera links (sensitive)
#   ./scripts/token.sh rm  <name>            cuts their access
#   ./scripts/token.sh ls                    lists who has access
#   ./scripts/token.sh regen                 regenerates Caddy's matcher
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

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ARCHIVO_TOKENS="$RAIZ/secrets/tokens.txt"
ARCHIVO_CADDY="$RAIZ/caddy/tokens.caddy"
GENERADOR="$RAIZ/scripts/token-matcher.py"
COMPOSE=(docker compose -f "$RAIZ/docker-compose.yml")

mkdir -p "$RAIZ/secrets"
touch "$ARCHIVO_TOKENS"
chmod 600 "$ARCHIVO_TOKENS"

aleatorio() {
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex 32
  else
    head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n'
  fi
}

# Normalises the expiry: "" | "30d" | "2026-12-31" -> "" | "YYYY-MM-DD".
# Exits with an error if the date is invalid or already past.
normalizar_vencimiento() {
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
# regenerar_matcher restores the previous file.
validar_caddy() {
  if "${COMPOSE[@]}" ps --status running --services 2>/dev/null | grep -qx caddy; then
    "${COMPOSE[@]}" exec -T caddy \
      caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null 2>&1
  else
    docker run --rm -v "$RAIZ/caddy:/etc/caddy:ro" caddy:2-alpine \
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
regenerar_matcher() {
  local nuevo="$ARCHIVO_CADDY.nuevo"
  local respaldo
  respaldo="$(mktemp)"

  if ! python3 "$GENERADOR" "$ARCHIVO_TOKENS" "$nuevo"; then
    echo "ERROR: could not generate the matcher; the current config was not touched." >&2
    rm -f "$nuevo"
    return 1
  fi

  [[ -f "$ARCHIVO_CADDY" ]] && cat "$ARCHIVO_CADDY" > "$respaldo"

  # In-place write: keeps the inode the container has mounted.
  cat "$nuevo" > "$ARCHIVO_CADDY"
  rm -f "$nuevo"

  if ! validar_caddy; then
    echo "ERROR: Caddy does not accept the generated config. Restoring the previous one." >&2
    cat "$respaldo" > "$ARCHIVO_CADDY"
    rm -f "$respaldo"
    echo "See the detail with:" >&2
    echo "  docker run --rm -v \"$RAIZ/caddy:/etc/caddy:ro\" caddy:2-alpine caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile" >&2
    return 1
  fi

  rm -f "$respaldo"
  return 0
}

recargar_caddy() {
  if docker compose -f "$RAIZ/docker-compose.yml" ps --status running --services 2>/dev/null | grep -qx caddy; then
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
  public="$(grep -E '^PUBLIC_URL=' "$RAIZ/.env" 2>/dev/null | cut -d= -f2- || true)"
  public="${public:-http://localhost:8090}"
  printf '%s' "${public%/}"
}

url_de() {
  printf '%s/?t=%s\n' "$(public_url)" "$1"
}

# The camera URL for players (VLC, mpv, ffmpeg).
#
# The token goes in the PATH, not in the query, and it is not a whim: the HLS
# playlist points at the segments with relative paths, and the player requests
# them without the query. With ?t=... the playlist downloads and the segments
# give 404. With the token in a path segment, every relative request inherits it
# on its own.
url_cam_de() {
  printf '%s/cam/%s/index.m3u8\n' "$(public_url)" "$1"
}

# The name is used inside ERE expressions (^name ) and as a work file.
# Restricting it to a charset with no metacharacters closes the regex injection
# surface: with no ., *, +, (, ), [, ] in the name, the pattern `^${nombre} `
# matches only that person.
validar_nombre() {
  if ! [[ "$1" =~ ^[A-Za-z0-9_-]+$ ]]; then
    echo "Invalid name: '$1'. Use only letters, numbers, hyphen or underscore." >&2
    exit 1
  fi
}

# Consistency between the site and the cookie. Production over HTTPS must set
# the Secure flag or the browser may send the session cookie over plain HTTP.
# Fail fast on unsafe production profiles (audit O4/#89). Set
# ALLOW_INSECURE_COOKIE=1 only for intentional non-production exceptions.
validar_entorno() {
  local sitio publico flags
  sitio="$(grep -E '^SITE_ADDRESS=' "$RAIZ/.env" 2>/dev/null | cut -d= -f2- || true)"
  publico="$(grep -E '^PUBLIC_URL=' "$RAIZ/.env" 2>/dev/null | cut -d= -f2- || true)"
  flags="$(grep -E '^COOKIE_FLAGS=' "$RAIZ/.env" 2>/dev/null | cut -d= -f2- || true)"
  if [[ ( "$sitio" == https://* || "$publico" == https://* ) && "$flags" != *Secure* ]]; then
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
  local nombre="${1:?usage: token.sh reveal <name>}"
  validar_nombre "$nombre"
  if ! grep -qE "^${nombre} " "$ARCHIVO_TOKENS"; then
    echo "\"$nombre\" was not on the list." >&2
    exit 1
  fi
  local t v
  t="$(awk -v n="$nombre" '$1==n {print $2}' "$ARCHIVO_TOKENS")"
  v="$(awk -v n="$nombre" '$1==n {print $4}' "$ARCHIVO_TOKENS")"
  echo "Credential for \"$nombre\" (sensitive - do not share publicly/log):"
  [[ -n "$v" ]] && echo "  expires $v"
  echo
  echo "  Invitation (browser): $(url_de "$t")"
  echo "  Camera (VLC/mpv):    $(url_cam_de "$t")"
}

cmd_add() {
  local nombre="${1:?usage: token.sh add <name> [expiry]}"
  local pedido="${2:-}"
  local vence

  validar_nombre "$nombre"
  validar_entorno

  # CAREFUL: after `if ! cmd`, $? is the negation's (always 0).
  # The code has to be saved before negating it.
  local rc=0
  vence="$(normalizar_vencimiento "$pedido")" || rc=$?
  if [ "$rc" -ne 0 ]; then
    case "$rc" in
      3) echo "That date has already passed: '$pedido'" >&2 ;;
      *) echo "Invalid expiry: '$pedido'. Use 30d or 2026-12-31" >&2 ;;
    esac
    exit 1
  fi

  if grep -qE "^${nombre} " "$ARCHIVO_TOKENS"; then
    local v
    v="$(awk -v n="$nombre" '$1==n {print $4}' "$ARCHIVO_TOKENS")"
    echo "\"$nombre\" already has access." >&2
    [[ -n "$v" ]] && echo "(expires $v)" >&2
    echo "Credential links are sensitive and are not printed by default." >&2
    echo "Run: ./scripts/token.sh reveal \"$nombre\"" >&2
    return 0
  fi

  local t
  t="$(aleatorio)"
  printf '%s %s %s%s\n' "$nombre" "$t" "$(aleatorio)" "${vence:+ $vence}" >> "$ARCHIVO_TOKENS"
  regenerar_matcher
  echo "OK access granted to \"$nombre\""
  if [[ -n "$vence" ]]; then
    echo "   expires $vence (that day included)"
  else
    echo "   no expiry set (long-lived). Prefer an expiry, e.g. ./scripts/token.sh add \"$nombre\" 30d" >&2
  fi
  echo
  echo "  Credential links are sensitive (bearer tokens) and are not printed by default."
  echo "  To display the invitation and camera URLs now, run:"
  echo "    ./scripts/token.sh reveal \"$nombre\""
  echo
  echo "  Treat revealed links as secrets: avoid logs, shell history, screenshots, and unencrypted chats."
  echo "  Revoke when no longer needed: ./scripts/token.sh rm \"$nombre\""
  echo
  recargar_caddy
}

cmd_rm() {
  local nombre="${1:?usage: token.sh rm <name>}"
  validar_nombre "$nombre"
  if ! grep -qE "^${nombre} " "$ARCHIVO_TOKENS"; then
    echo "\"$nombre\" was not on the list." >&2
    exit 1
  fi
  # The `|| true` is not decorative: if it is the LAST token, grep -v returns no
  # lines, exits with 1, and with `set -e` the script died here without warning.
  grep -vE "^${nombre} " "$ARCHIVO_TOKENS" > "$ARCHIVO_TOKENS.tmp" || true
  mv "$ARCHIVO_TOKENS.tmp" "$ARCHIVO_TOKENS"
  regenerar_matcher
  echo "OK access revoked for \"$nombre\" (token and cookie)"
  recargar_caddy
}

cmd_ls() {
  if ! [[ -s "$ARCHIVO_TOKENS" ]]; then
    echo "(nobody has access yet)"
    return 0
  fi
  local hoy; hoy="$(date +%F)"
  printf '%-20s %-16s %s\n' "WHO" "TOKEN" "EXPIRES"
  while read -r n t _ v; do
    [[ -z "${n:-}" ]] && continue
    if [[ -z "${v:-}" ]]; then
      printf '%-20s %-16s %s\n' "$n" "${t:0:12}..." "never"
    elif [[ "$v" < "$hoy" ]]; then
      printf '%-20s %-16s %s  <-- EXPIRED\n' "$n" "${t:0:12}..." "$v"
    else
      printf '%-20s %-16s %s\n' "$n" "${t:0:12}..." "$v"
    fi
  done < "$ARCHIVO_TOKENS"
}

cmd_regen() {
  validar_entorno
  regenerar_matcher
  echo "OK caddy/tokens.caddy regenerated from secrets/tokens.txt"
  recargar_caddy
}

cmd_check() {
  validar_entorno
  echo "OK production/config checks passed (cookie/transport basics)."
}

case "${1:-}" in
  add) shift; cmd_add "$@" ;;
  reveal|show) shift; cmd_reveal "$@" ;;
  rm|del|remove) shift; cmd_rm "$@" ;;
  regen|sync) cmd_regen ;;
  check|doctor) cmd_check ;;
  ls|list) cmd_ls ;;
  *) sed -n '2,/^set -euo/p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' | sed '$d'; exit 1 ;;
esac
