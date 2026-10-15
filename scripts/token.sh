#!/usr/bin/env bash
# token.sh — gestiona quién puede escuchar la radio.
#
#   ./scripts/token.sh add <nombre>     da acceso a esa persona
#   ./scripts/token.sh rm  <nombre>     le corta el acceso
#   ./scripts/token.sh ls               lista quién tiene acceso
#
# Cada persona recibe DOS credenciales:
#
#   token    — viaja una sola vez, en el link de invitación (?t=...).
#              El navegador lo canjea por la cookie y lo suelta.
#              Queda disponible para reproductores (VLC, mpv, ffmpeg),
#              que no manejan cookies.
#
#   cookieid — lo que el navegador manda en cada request después del
#              canje. Gracias a esto el token no aparece en la URL ni en
#              los logs de Caddy en cada reproducción.
#
# Las dos se revocan juntas con `rm`.

set -euo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ARCHIVO_TOKENS="$RAIZ/secrets/tokens.txt"
ARCHIVO_CADDY="$RAIZ/caddy/tokens.caddy"
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

# Lee tokens.txt, completa cookieids faltantes (migración de formato viejo),
# reescribe el archivo normalizado y regenera caddy/tokens.caddy.
regenerar_matcher() {
  python3 - "$ARCHIVO_TOKENS" "$ARCHIVO_CADDY" <<'PY'
import json, secrets, sys

entrada, salida = sys.argv[1], sys.argv[2]

filas = []
cambio = False
with open(entrada, encoding="utf-8") as fh:
    for linea in fh:
        linea = linea.strip()
        if not linea or linea.startswith("#"):
            continue
        partes = linea.split()
        nombre, token = partes[0], partes[1]
        if len(partes) >= 3:
            cookie = partes[2]
        else:
            cookie = secrets.token_hex(32)   # migración: formato viejo de 2 campos
            cambio = True
        filas.append((nombre, token, cookie))

# Normaliza el archivo solo si hubo migración.
if cambio:
    with open(entrada, "w", encoding="utf-8") as fh:
        for n, t, c in filas:
            fh.write("%s %s %s\n" % (n, t, c))

tokens = ", ".join(json.dumps(t) for _, t, _ in filas)
cookies = ", ".join(json.dumps(c) for _, _, c in filas)

cuerpo = [
    "# GENERADO POR scripts/token.sh — NO EDITAR A MANO.",
    "# Define los matchers @gate y @canje que usa el Caddyfile.",
    "# Sin credenciales válidas acá, nadie entra.",
]

if filas:
    cuerpo += [
        "",
        "# @gate: entra quien tenga el token (reproductores) o la cookie (navegador).",
        "@gate {",
        "\texpression `{http.request.uri.query.t} in [%s] || {http.request.cookie.radio} in [%s]`" % (tokens, cookies),
        "}",
        "",
        "# @canje: la pantalla del reproductor con ?t=... se convierte en cookie",
        "# y la URL queda limpia. Solo en la raíz, para no romper /stream.",
        "@canje {",
        "\tpath /",
        "\texpression `{http.request.uri.query.t} in [%s]`" % tokens,
        "}",
    ]
else:
    cuerpo += [
        "",
        "# No hay credenciales: todo el sitio responde 404.",
        "@gate expression `false`",
    ]

with open(salida, "w", encoding="utf-8") as fh:
    fh.write("\n".join(cuerpo) + "\n")
PY
}

recargar_caddy() {
  if docker compose -f "$RAIZ/docker-compose.yml" ps --status running --services 2>/dev/null | grep -qx caddy; then
    echo "-> recargando Caddy (sin cortar a los oyentes)..."
    if "${COMPOSE[@]}" exec -T caddy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null 2>&1; then
      echo "-> listo."
    else
      echo "-> no se pudo recargar en caliente; reinicio el contenedor." >&2
      "${COMPOSE[@]}" restart caddy >/dev/null
      echo "-> listo."
    fi
  else
    echo "-> Caddy no está corriendo; el cambio se aplica al levantarlo."
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
    local t
    t="$(awk -v n="$nombre" '$1==n {print $2}' "$ARCHIVO_TOKENS")"
    echo ""$nombre" ya tenía acceso. Su link sigue siendo:" >&2
    url_de "$t"
    return 0
  fi
  local t
  t="$(aleatorio)"
  printf '%s %s %s\n' "$nombre" "$t" "$(aleatorio)" >> "$ARCHIVO_TOKENS"
  regenerar_matcher
  echo "OK acceso dado a "$nombre""
  echo
  echo "  Pasale este link:"
  echo "  $(url_de "$t")"
  echo
  echo "  Al abrirlo en el navegador, el token se canjea por una cookie y"
  echo "  desaparece de la URL. Para VLC/mpv sirve el mismo link."
  echo
  recargar_caddy
}

cmd_rm() {
  local nombre="${1:?uso: token.sh rm <nombre>}"
  if ! grep -qE "^${nombre} " "$ARCHIVO_TOKENS"; then
    echo ""$nombre" no estaba en la lista." >&2
    exit 1
  fi
  grep -vE "^${nombre} " "$ARCHIVO_TOKENS" > "$ARCHIVO_TOKENS.tmp"
  mv "$ARCHIVO_TOKENS.tmp" "$ARCHIVO_TOKENS"
  regenerar_matcher
  echo "OK acceso revocado a "$nombre" (token y cookie)"
  recargar_caddy
}

cmd_ls() {
  if ! [[ -s "$ARCHIVO_TOKENS" ]]; then
    echo "(nadie tiene acceso todavía)"
    return 0
  fi
  printf '%-20s %-16s %s\n' "QUIEN" "TOKEN" "COOKIE"
  while read -r n t c; do
    [[ -z "${n:-}" ]] && continue
    printf '%-20s %-16s %s\n' "$n" "${t:0:12}..." "${c:0:12}..."
  done < "$ARCHIVO_TOKENS"
}

cmd_regen() {
  regenerar_matcher
  echo "OK caddy/tokens.caddy regenerado desde secrets/tokens.txt"
  recargar_caddy
}

case "${1:-}" in
  add) shift; cmd_add "$@" ;;
  rm|del|remove) shift; cmd_rm "$@" ;;
  regen|sync) cmd_regen ;;
  ls|list) cmd_ls ;;
  *) sed -n '2,20p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 1 ;;
esac
