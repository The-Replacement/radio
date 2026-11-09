#!/usr/bin/env bash
# claims.sh — la promesa de F-13 y F-06, medida: la config que va al sitio
# nadie escribe a mano el dominio de producción. Todo pasa por el seam
# FRONTEND_URL (Caddyfile, el JS del player, smoke, token.sh).
#
# Sale 0 si todas las ocurrencias de radio.daza.ar están en la allowlist
# de acá abajo; sale 1 si aparece una nueva. La allowlist completa y con
# nombre y apellido; nada más se cuela.
#
# Busca con git grep sobre el árbol que se despliega (los configs + los
# docs vivos del sitio, no los PLAN-LEGAL ni el prompt de la auditoría).
set -uo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$RAIZ"

VERDE=$'\033[32m'; ROJO=$'\033[31m'; FIN=$'\033[0m'

# Allowlist documentada, una línea por permiso explícito. Todo lo que no
# esté acá, en esta config, es un hardcode nuevo y el gate se pone rojo.
#   .env.example  — la línea documentada del seam (S8, F-06 / F-13)
#   web/index.html — la firma pública de la estación, no un origin
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
echo "Claims — nadie escribe a mano el dominio en la config"
echo

mapfile -t HITS < <(git grep -niF 'radio.daza.ar' -- \
  caddy web scripts docker-compose.yml .env.example \
  icecast liquidsoap cam mediamtx 2>/dev/null || true)

malas=0
buenas=0
for hit in "${HITS[@]}"; do
  archivo="${hit%%:*}"
  resto="${hit#*:}"
  contenido="${resto#*:}"
  linea="${resto%%:*}"
  if permitida "$archivo" "$contenido"; then
    printf '  %sOK%s    %-28s :%s — allowlist documentada\n' "$VERDE" "$FIN" "$archivo" "$linea"
    buenas=$((buenas + 1))
  else
    printf '  %sFALLA%s %s:%s — %s\n' "$ROJO" "$FIN" "$archivo" "$linea" "$contenido"
    malas=$((malas + 1))
  fi
done

echo
if [[ "$malas" -eq 0 ]]; then
  printf '%sTodo bien%s — %d ocurrencia(s), todas en la allowlist. El seam está intacto.\n\n' "$VERDE" "$FIN" "$buenas"
  exit 0
else
  printf '%s%d ocurrencia(s) sin allowlist%s — el dominio vuelve a estar escrito a mano.\n' "$ROJO" "$malas" "$FIN"
  echo
  echo "Arreglalo por el seam (FRONTEND_URL) o, si de verdad hace falta, escribí la"
  echo "línea en PERMITIDAS con su justificación — una línea concreta, a la vista."
  echo
  exit 1
fi
