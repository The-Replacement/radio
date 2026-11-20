#!/usr/bin/env bash
# claims.sh — the promise of F-13 and F-06, measured: in the config that goes
# to the site nobody hand-writes the production domains. Everything goes through
# the FRONTEND_URL / RADIO_BACKEND_URL seams (Caddyfile, the player's JS, smoke,
# token.sh).
#
# It exits 0 if every occurrence of comechingona.daza.ar or stream.daza.ar is on
# the allowlist below; it exits 1 if a new one appears. The allowlist is complete
# and explicit; nothing else slips in.
#
# It searches with git grep over the tree that is deployed (the configs + the
# site's living docs, not the PLAN-LEGAL nor the audit prompt).
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 1

GREEN=$'\033[32m'; RED=$'\033[31m'; RESET=$'\033[0m'

# The documented allowlist, one line per explicit permission. Anything not here,
# in this config, is a new hardcode and the gate goes red.
#   .env.example   — seam/documentation lines (S8, F-06 / F-13; door seams)
#   web/index.html — the station's public signature, not an origin
#   caddy/Caddyfile — prose comments documenting the split/backend host
# claims.sh does not scan itself: the script literally contains the allowed
# lines BY definition.
ALLOWED=(
  '.env.example|FRONTEND_URL=https://comechingona.daza.ar'
  '.env.example|Production:  stream.daza.ar'
  'web/index.html|comechingona.daza.ar · sierras de los comechingones'
  'caddy/Caddyfile|backend (stream.daza.ar)'
  'caddy/Caddyfile|host-only para stream.daza.ar'
)

allowed() {
  local archivo="$1" contenido="$2" p file_ok string_ok
  for p in "${ALLOWED[@]}"; do
    file_ok="${p%%|*}"
    string_ok="${p#*|}"
    if [[ "$archivo" == "$file_ok" && "$contenido" == *"$string_ok"* ]]; then
      return 0
    fi
  done
  return 1
}

echo
echo "Claims — nobody hand-writes the production domains in the config"
echo

mapfile -t HITS < <(git grep -niE 'comechingona\.daza\.ar|stream\.daza\.ar' -- \
  caddy web docker-compose.yml .env.example \
  icecast liquidsoap cam mediamtx ':!scripts/claims.sh' 2>/dev/null || true)

bad_count=0
good_count=0
for hit in "${HITS[@]}"; do
  archivo="${hit%%:*}"
  resto="${hit#*:}"
  contenido="${resto#*:}"
  linea="${resto%%:*}"
  if allowed "$archivo" "$contenido"; then
    printf '  %sOK%s    %-28s :%s — documented allowlist\n' "$GREEN" "$RESET" "$archivo" "$linea"
    good_count=$((good_count + 1))
  else
    printf '  %sFAIL%s %s:%s — %s\n' "$RED" "$RESET" "$archivo" "$linea" "$contenido"
    bad_count=$((bad_count + 1))
  fi
done

echo
if [[ "$bad_count" -eq 0 ]]; then
  printf '%sAll good%s — %d occurrence(s), all on the allowlist. The seam is intact.\n\n' "$GREEN" "$RESET" "$good_count"
  exit 0
else
  printf '%s%d occurrence(s) without allowlist%s — the domain is hand-written again.\n' "$RED" "$bad_count" "$RESET"
  echo
  echo "Fix it through the seam (FRONTEND_URL) or, if it is really needed, write the"
  echo "line in ALLOWED with its justification — one concrete line, in plain sight."
  echo
  exit 1
fi
