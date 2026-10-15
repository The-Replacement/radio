#!/usr/bin/env bash
# smoke.sh — verifica que la radio esté arriba Y que la puerta cierre bien.
#
# Lo importante que prueba este script: que SIN token no se vea nada.
# Una radio "privada" que se ve sin token no es privada.

set -uo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BASE="${BASE:-http://localhost:8090}"

ok=0; mal=0
pasa() { printf '  \033[32m✓\033[0m %s\n' "$1"; ok=$((ok+1)); }
falla() { printf '  \033[31m✗\033[0m %s\n' "$1"; mal=$((mal+1)); }

echo
echo "Smoke test — $BASE"
echo

# 1. Sin token, el player no debe existir.
code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$BASE/" || echo 000)
[[ "$code" == "404" ]] && pasa "sin token → 404 (no revela que hay una radio)" \
                       || falla "sin token → $code (esperaba 404)"

# 2. Sin token, el stream tampoco.
code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$BASE/stream" || echo 000)
[[ "$code" == "404" ]] && pasa "stream sin token → 404" \
                       || falla "stream sin token → $code (esperaba 404)"

# 3. Token inventado: igual 404.
code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$BASE/stream?t=basura" || echo 000)
[[ "$code" == "404" ]] && pasa "token inválido → 404" \
                       || falla "token inválido → $code (esperaba 404)"

# 4. Con token válido, el player existe.
TOKEN="$(awk 'NF>=2 {print $2; exit}' "$RAIZ/secrets/tokens.txt" 2>/dev/null || true)"
if [[ -z "$TOKEN" ]]; then
  falla "no hay ningún token en secrets/tokens.txt (corré: scripts/token.sh add vos)"
else
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$BASE/?t=$TOKEN" || echo 000)
  [[ "$code" == "200" ]] && pasa "con token → player 200" \
                         || falla "con token → player $code (esperaba 200)"

  # 5. Con token válido, el stream entrega audio de verdad.
  len=$(curl -s --max-time 8 "$BASE/stream?t=$TOKEN" | head -c 65536 | wc -c)
  if [[ "${len:-0}" -gt 4096 ]]; then
    pasa "con token → stream entrega ${len} bytes de audio"
  else
    falla "con token → stream entregó ${len} bytes (¿Liquidsoap está conectado? ¿hay música en ./music?)"
  fi

  # 6. Estado / now playing.
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$BASE/status-json.xsl?t=$TOKEN" || echo 000)
  [[ "$code" == "200" ]] && pasa "con token → status 200" \
                         || falla "con token → status $code"
fi

echo
if [[ "$mal" -eq 0 ]]; then
  printf '\033[32mTodo bien\033[0m — %d pruebas pasaron.\n\n' "$ok"
  exit 0
else
  printf '\033[31m%d fallas\033[0m de %d pruebas.\n' "$mal" "$((ok+mal))"
  echo "Sugerencias: docker compose logs -f liquidsoap   (¿conecta a Icecast?)"
  echo "             docker compose logs -f icecast      (¿algún error de config?)"
  echo
  exit 1
fi
