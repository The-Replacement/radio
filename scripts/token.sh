#!/usr/bin/env bash
# token.sh — gestiona quién puede escuchar la radio.
#
#   ./scripts/token.sh add <nombre>     da un token nuevo a esa persona
#   ./scripts/token.sh rm  <nombre>     le corta el acceso
#   ./scripts/token.sh ls               lista quién tiene acceso
#
# Un token por persona: así podés cortarle el acceso a una sola
# persona sin cambiarle el link a las demás.

set -euo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ARCHIVO_TOKENS="$RAIZ/secrets/tokens.txt"
ARCHIVO_CADDY="$RAIZ/caddy/tokens.caddy"
COMPOSE=(docker compose -f "$RAIZ/docker-compose.yml")

mkdir -p "$RAIZ/secrets"
touch "$ARCHIVO_TOKENS"
chmod 600 "$ARCHIVO_TOKENS"

nuevo_token() {
  # 32 bytes en hex = 64 caracteres. Adivinarlo no es una opción.
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex 32
  else
    head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n'
  fi
}

regenerar_matcher() {
  # Lee "nombre token" por línea y arma el matcher CEL de Caddy.
  python3 - "$ARCHIVO_TOKENS" "$ARCHIVO_CADDY" <<'PY'
import json, sys

entrada, salida = sys.argv[1], sys.argv[2]
pares = []
with open(entrada, encoding="utf-8") as fh:
    for linea in fh:
        linea = linea.strip()
        if not linea or linea.startswith("#"):
            continue
        partes = linea.split()
        if len(partes) >= 2:
            pares.append((partes[0], partes[1]))

lista = ", ".join(json.dumps(t) for _, t in pares)

cuerpo = [
    "# GENERADO POR scripts/token.sh — NO EDITAR A MANO.",
    "# Este archivo define el matcher @gate que usa el Caddyfile.",
    "# Sin tokens válidos acá, nadie entra.",
]
if pares:
    cuerpo += [
        "@gate {",
        "\texpression `{http.request.uri.query.t} in [%s]`" % lista,
        "}",
    ]
else:
    cuerpo += [
        "# No hay tokens: todo el sitio responde 404.",
        "@gate expression `false`",
    ]

with open(salida, "w", encoding="utf-8") as fh:
    fh.write("\n".join(cuerpo) + "\n")
PY
}

recargar_caddy() {
  if docker compose -f "$RAIZ/docker-compose.yml" ps --status running --services 2>/dev/null | grep -qx caddy; then
    echo "→ recargando Caddy (sin cortar a los oyentes)…"
    if "${COMPOSE[@]}" exec -T caddy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null 2>&1; then
      echo "→ listo."
    else
      echo "→ no se pudo recargar en caliente; reinicio el contenedor." >&2
      "${COMPOSE[@]}" restart caddy >/dev/null
      echo "→ listo."
    fi
  else
    echo "→ Caddy no está corriendo; el cambio se aplica al levantarlo."
  fi
}

url_de() {
  local t="$1"
  local public
  public="$(grep -E '^PUBLIC_URL=' "$RAIZ/.env" 2>/dev/null | cut -d= -f2- || true)"
  public="${public:-http://localhost:8090}"
  public="${public%/}"
  printf '%s/?t=%s\n' "$public" "$t"
}

cmd_add() {
  local nombre="${1:?uso: token.sh add <nombre>}"
  if grep -qE "^${nombre} " "$ARCHIVO_TOKENS"; then
    echo "«$nombre» ya tenía acceso. Token actual:" >&2
    awk -v n="$nombre" '$1==n {print $2}' "$ARCHIVO_TOKENS"
    return 0
  fi
  local t
  t="$(nuevo_token)"
  printf '%s %s\n' "$nombre" "$t" >> "$ARCHIVO_TOKENS"
  regenerar_matcher
  echo "✓ acceso dado a «$nombre»"
  echo
  echo "  Link para pasarle:"
  echo "  $(url_de "$t")"
  echo
  recargar_caddy
}

cmd_rm() {
  local nombre="${1:?uso: token.sh rm <nombre>}"
  if ! grep -qE "^${nombre} " "$ARCHIVO_TOKENS"; then
    echo "«$nombre» no estaba en la lista." >&2
    exit 1
  fi
  grep -vE "^${nombre} " "$ARCHIVO_TOKENS" > "$ARCHIVO_TOKENS.tmp"
  mv "$ARCHIVO_TOKENS.tmp" "$ARCHIVO_TOKENS"
  regenerar_matcher
  echo "✓ acceso revocado a «$nombre»"
  recargar_caddy
}

cmd_ls() {
  if ! [[ -s "$ARCHIVO_TOKENS" ]]; then
    echo "(nadie tiene acceso todavía)"
    return 0
  fi
  printf '%-20s %s\n' "QUIÉN" "TOKEN (primeros 12)"
  while read -r n t; do
    [[ -z "${n:-}" ]] && continue
    printf '%-20s %s…\n' "$n" "${t:0:12}"
  done < "$ARCHIVO_TOKENS"
}

case "${1:-}" in
  add) shift; cmd_add "$@" ;;
  rm|del|remove) shift; cmd_rm "$@" ;;
  ls|list) cmd_ls ;;
  *) sed -n '2,9p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 1 ;;
esac
