#!/usr/bin/env bash
# claims.sh — the promise of F-13 and F-06, measured: in the config that goes
# to the site nobody hand-writes the production domain. Everything goes through
# the FRONTEND_URL seam (Caddyfile, the player's JS, smoke, token.sh).
#
# It exits 0 if every occurrence of radio.daza.ar is on the allowlist below; it
# exits 1 if a new one appears. The allowlist is complete and explicit; nothing
# else slips in.
#
# It searches with git grep over the tree that is deployed (the configs + the
# site's living docs, not the PLAN-LEGAL nor the audit prompt).
set -uo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$RAIZ"

VERDE=$'\033[32m'; ROJO=$'\033[31m'; FIN=$'\033[0m'

# The documented allowlist, one line per explicit permission. Anything not here,
# in this config, is a new hardcode and the gate goes red.
#   .env.example  — the seam's documented line (S8, F-06 / F-13)
#   web/index.html — the station's public signature, not an origin
# claims.sh does not scan itself: the script literally contains the allowed
# lines BY definition.
PERMITIDAS=(
  '.env.example|FRONTEND_URL=https://radio.daza.ar'
  'web/index.html|radio.daza.ar · sierras de los comechingones'
)

permitida() {
  local archivo="$1" contenido="$2" p archivo_ok cadena_ok
  for p in "${PERMITIDAS[@]}"; do
    archivo_ok="${p%%|*}"
    cadena_ok="${p#*|}"
    if [[ "$archivo" == "$archivo_ok" && "$contenido" == *"$cadena_ok"* ]]; then
      return 0
    fi
  done
  return 1
}

echo
echo "Claims — nobody hand-writes the domain in the config"
echo

mapfile -t HITS < <(git grep -niF 'radio.daza.ar' -- \
  caddy web docker-compose.yml .env.example \
  icecast liquidsoap cam mediamtx ':!scripts/claims.sh' 2>/dev/null || true)

malas=0
buenas=0
for hit in "${HITS[@]}"; do
  archivo="${hit%%:*}"
  resto="${hit#*:}"
  contenido="${resto#*:}"
  linea="${resto%%:*}"
  if permitida "$archivo" "$contenido"; then
    printf '  %sOK%s    %-28s :%s — documented allowlist\n' "$VERDE" "$FIN" "$archivo" "$linea"
    buenas=$((buenas + 1))
  else
    printf '  %sFAIL%s %s:%s — %s\n' "$ROJO" "$FIN" "$archivo" "$linea" "$contenido"
    malas=$((malas + 1))
  fi
done

echo
if [[ "$malas" -eq 0 ]]; then
  printf '%sAll good%s — %d occurrence(s), all on the allowlist. The seam is intact.\n\n' "$VERDE" "$FIN" "$buenas"
  exit 0
else
  printf '%s%d occurrence(s) without allowlist%s — the domain is hand-written again.\n' "$ROJO" "$malas" "$FIN"
  echo
  echo "Fix it through the seam (FRONTEND_URL) or, if it is really needed, write the"
  echo "line in PERMITIDAS with its justification — one concrete line, in plain sight."
  echo
  exit 1
fi
