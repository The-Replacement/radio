#!/usr/bin/env bash
# smoke.sh — verifica que la radio esté arriba Y que la puerta cierre bien.
#
# Lo importante que prueba este script: que SIN credencial no se vea nada,
# y que el canje de token por cookie funcione.
# Una radio "privada" que se ve sin credencial no es privada.

set -uo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BASE="${BASE:-http://localhost:8090}"

ok=0; mal=0
pasa() { printf '  \033[32mOK\033[0m %s\n' "$1"; ok=$((ok+1)); }
falla() { printf '  \033[31mFALLA\033[0m %s\n' "$1"; mal=$((mal+1)); }
codigo() { curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$@" 2>/dev/null || echo 000; }

# Para el stream, `codigo` NO sirve: el stream nunca termina, así que curl
# se queda leyendo hasta el timeout y no imprime el código.
# Guardamos las cabeceras en un archivo (llegan enseguida) y leemos la
# primera línea. No usamos `| head -n1` a propósito: el SIGPIPE haría que
# curl salga con error y, con `set -o pipefail`, contaminaría el resultado.
codigo_stream() {
  local f
  f="$(mktemp)"
  curl -s -o /dev/null -D "$f" --max-time 3 "$@" 2>/dev/null || true
  awk 'NR==1 {print $2}' "$f"
  rm -f "$f"
}

echo
echo "Smoke test — $BASE"
echo

echo "sin credencial:"
[[ "$(codigo "$BASE/")" == "404" ]] \
  && pasa "el reproductor -> 404 (no revela que hay una radio)" \
  || falla "el reproductor -> $(codigo "$BASE/") (esperaba 404)"
[[ "$(codigo_stream "$BASE/stream")" == "404" ]] \
  && pasa "el stream -> 404" || falla "el stream -> $(codigo_stream "$BASE/stream")"
[[ "$(codigo_stream -H 'Cookie: radio=basura' "$BASE/stream")" == "404" ]] \
  && pasa "cookie inválida -> 404" || falla "cookie inválida no dio 404"
[[ "$(codigo_stream "$BASE/stream?t=basura")" == "404" ]] \
  && pasa "token inválido -> 404" || falla "token inválido no dio 404"

TOKEN="$(awk 'NF>=2 {print $2; exit}' "$RAIZ/secrets/tokens.txt" 2>/dev/null || true)"
COOKIE="$(awk 'NF>=3 {print $3; exit}' "$RAIZ/secrets/tokens.txt" 2>/dev/null || true)"

if [[ -z "$TOKEN" ]]; then
  falla "no hay credenciales en secrets/tokens.txt (corré: scripts/token.sh add vos)"
else
  echo
  echo "canje del link por cookie:"
  # tr -d '\r': curl usa CRLF y el \r rompe los anclajes de fin de línea.
  CABECERAS="$(curl -s -i --max-time 10 "$BASE/?t=$TOKEN" 2>/dev/null | tr -d '\r' || true)"
  grep -qiE '^set-cookie: radio=' <<<"$CABECERAS" \
    && pasa "el link de invitación deja la cookie" \
    || falla "el link de invitación NO dejó la cookie"
  grep -qE '^HTTP/[0-9.]+ 302' <<<"$CABECERAS" \
    && pasa "y redirige a la URL limpia (302)" \
    || falla "no redirigió con 302"
  grep -qiE '^location: /$' <<<"$CABECERAS" \
    && pasa "la redirección no arrastra el token" \
    || falla "la redirección arrastra el token"

  echo
  echo "con credencial válida:"
  [[ "$(codigo "$BASE/?t=$TOKEN")" == "302" ]] \
    && pasa "token -> canje" || falla "token no canjeó"

  if [[ -n "$COOKIE" ]]; then
    [[ "$(codigo_stream -H "Cookie: radio=$COOKIE" "$BASE/stream")" == "200" ]] \
      && pasa "cookie -> stream 200" || falla "cookie no autenticó el stream"
    [[ "$(codigo -H "Cookie: radio=$COOKIE" "$BASE/")" == "200" ]] \
      && pasa "cookie -> reproductor 200" || falla "cookie no sirvió el reproductor"
    [[ "$(codigo -H "Cookie: radio=$COOKIE" "$BASE/status-json.xsl")" == "200" ]] \
      && pasa "cookie -> status 200" || falla "cookie no sirvió el status"
  else
    falla "no hay cookieid: corré scripts/token.sh ls y regenerá con add/rm"
  fi

  # El stream tiene que entregar audio de verdad, no solo un 200.
  len=$(curl -s --max-time 8 "$BASE/stream?t=$TOKEN" | head -c 65536 | wc -c)
  if [[ "${len:-0}" -gt 4096 ]]; then
    pasa "el stream entrega ${len} bytes de audio"
  else
    falla "el stream entregó ${len} bytes (¿Liquidsoap conectado? ¿hay música en ./music?)"
  fi

  # Que el token NO viaje hacia Icecast.
  if docker compose -f "$RAIZ/docker-compose.yml" ps --status running --services 2>/dev/null | grep -qx icecast; then
    curl -s -o /dev/null --max-time 5 "$BASE/stream?t=$TOKEN" 2>/dev/null || true
    if docker compose -f "$RAIZ/docker-compose.yml" logs --no-log-prefix icecast 2>/dev/null | grep -q 'stream?t='; then
      falla "el token aparece en los logs de Icecast (el rewrite no está filtrando)"
    else
      pasa "el token NO llega a Icecast"
    fi
  fi
fi

echo
if [[ "$mal" -eq 0 ]]; then
  printf '\033[32mTodo bien\033[0m — %d pruebas pasaron.\n\n' "$ok"
  exit 0
else
  printf '\033[31m%d fallas\033[0m de %d pruebas.\n' "$mal" "$((ok+mal))"
  echo "Sugerencias: docker compose logs -f caddy        (¿recargó bien?)"
  echo "             docker compose logs -f liquidsoap   (¿conecta a Icecast?)"
  echo
  exit 1
fi
