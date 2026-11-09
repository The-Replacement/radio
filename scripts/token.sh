#!/usr/bin/env bash
# token.sh - gestiona quien puede escuchar la radio.
#
#   ./scripts/token.sh add <nombre> [vencimiento]   da acceso
#   ./scripts/token.sh rm  <nombre>                 le corta el acceso
#   ./scripts/token.sh ls                           lista quien tiene acceso
#   ./scripts/token.sh regen                        regenera el matcher de Caddy
#
# El vencimiento es opcional y acepta dos formas:
#
#   ./scripts/token.sh add juan 30d          vence en 30 dias
#   ./scripts/token.sh add juan 2026-12-31   vence esa fecha (ese dia inclusive)
#
# Sin vencimiento, la credencial dura para siempre.
#
# Cada persona recibe DOS credenciales:
#
#   token    - viaja una sola vez, en el link de invitacion (?t=...).
#              El navegador lo canjea por la cookie y lo suelta.
#              Queda disponible para reproductores (VLC, mpv, ffmpeg),
#              que no manejan cookies.
#
#   cookieid - lo que el navegador manda en cada request despues del
#              canje. Gracias a esto el token no aparece en la URL ni en
#              los logs de Caddy en cada reproduccion.
#
# Las dos se revocan juntas con `rm`.
#
# El vencimiento se aplica en Caddy, comparando contra la fecha actual:
# no hace falta correr nada cuando una credencial caduca.

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

# Normaliza el vencimiento: "" | "30d" | "2026-12-31" -> "" | "YYYY-MM-DD".
# Sale con error si la fecha es inválida o ya pasó.
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

# Valida la configuracion con el propio Caddy antes de recargar.
# Un matcher roto no tiene que poder voltear la radio: si esto falla,
# regenerar_matcher restaura el archivo anterior.
validar_caddy() {
  if "${COMPOSE[@]}" ps --status running --services 2>/dev/null | grep -qx caddy; then
    "${COMPOSE[@]}" exec -T caddy \
      caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null 2>&1
  else
    docker run --rm -v "$RAIZ/caddy:/etc/caddy:ro" caddy:2-alpine \
      caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null 2>&1
  fi
}

# Regenera caddy/tokens.caddy desde secrets/tokens.txt.
# El generador tambien migra el formato viejo y omite los vencidos.
#
# DOS COSAS DELICADAS, las dos ya nos mordieron:
#
# 1) NO usar `mv` para reemplazar el archivo. Docker bind-montea tokens.caddy
#    por INODO: si el reemplazo cambia el inodo, el contenedor sigue viendo
#    la version vieja y `caddy reload` recarga basura sin efecto. Hay que
#    escribir IN PLACE (`cat ... > archivo`), que trunca y reusa el inodo.
#
# 2) Validar antes de dar por buena la config. Un matcher que no compila en
#    CEL deja a Caddy en loop de reinicio: la radio entera se cae.
regenerar_matcher() {
  local nuevo="$ARCHIVO_CADDY.nuevo"
  local respaldo
  respaldo="$(mktemp)"

  if ! python3 "$GENERADOR" "$ARCHIVO_TOKENS" "$nuevo"; then
    echo "ERROR: no se pudo generar el matcher; no se toco la config actual." >&2
    rm -f "$nuevo"
    return 1
  fi

  [[ -f "$ARCHIVO_CADDY" ]] && cat "$ARCHIVO_CADDY" > "$respaldo"

  # Escritura in place: conserva el inodo que el contenedor tiene montado.
  cat "$nuevo" > "$ARCHIVO_CADDY"
  rm -f "$nuevo"

  if ! validar_caddy; then
    echo "ERROR: Caddy no acepta la config generada. Restauro la anterior." >&2
    cat "$respaldo" > "$ARCHIVO_CADDY"
    rm -f "$respaldo"
    echo "Mira el detalle con:" >&2
    echo "  docker run --rm -v \"$RAIZ/caddy:/etc/caddy:ro\" caddy:2-alpine caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile" >&2
    return 1
  fi

  rm -f "$respaldo"
  return 0
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

# PUBLIC_URL desde .env, con default. Es lo único que comparten las dos
# funciones de abajo: el formato de cada URL es distinto (token en el query
# vs. token en el camino), así que sólo se extrae la lectura, no la forma.
public_url() {
  local public
  public="$(grep -E '^PUBLIC_URL=' "$RAIZ/.env" 2>/dev/null | cut -d= -f2- || true)"
  public="${public:-http://localhost:8090}"
  printf '%s' "${public%/}"
}

url_de() {
  printf '%s/?t=%s\n' "$(public_url)" "$1"
}

# URL de la camara para reproductores (VLC, mpv, ffmpeg).
#
# El token va en el CAMINO, no en el query, y no es capricho: el playlist de
# HLS apunta a los segmentos con rutas relativas, y el reproductor las pide
# sin el query. Con ?t=... el playlist baja y los segmentos dan 404. Con el
# token en un segmento del camino, cada pedido relativo lo hereda solo.
url_cam_de() {
  printf '%s/cam/%s/index.m3u8\n' "$(public_url)" "$1"
}

# El nombre se usa dentro de expresiones ERE (^nombre ) y como archivo de
# trabajo. Restringirlo a un charset sin metacaracteres cierra la superficie
# de inyección de regex: si no hay ., *, +, (, ), [, ] en el nombre, el patrón
# `^${nombre} ` matchea sólo esa persona.
validar_nombre() {
  if ! [[ "$1" =~ ^[A-Za-z0-9_-]+$ ]]; then
    echo "Nombre inválido: '$1'. Usá sólo letras, números, guion o guion bajo." >&2
    exit 1
  fi
}

# Coherencia entre el sitio y la cookie. No puedo reiniciar Caddy acá, así que
# es un aviso alto en las operaciones que sí tocan el .env: https sin Secure
# deja la cookie viajando por HTTP claro.
validar_entorno() {
  local sitio flags
  sitio="$(grep -E '^SITE_ADDRESS=' "$RAIZ/.env" 2>/dev/null | cut -d= -f2- || true)"
  flags="$(grep -E '^COOKIE_FLAGS=' "$RAIZ/.env" 2>/dev/null | cut -d= -f2- || true)"
  if [[ "$sitio" == https://* && "$flags" != *Secure* ]]; then
    echo "AVISO: SITE_ADDRESS usa https pero COOKIE_FLAGS no tiene 'Secure'." >&2
    echo "       La cookie de sesión se mandará por HTTP. Agregá '; Secure' a COOKIE_FLAGS." >&2
  fi
}

cmd_add() {
  local nombre="${1:?uso: token.sh add <nombre> [vencimiento]}"
  local pedido="${2:-}"
  local vence

  validar_nombre "$nombre"
  validar_entorno

  # OJO: despues de `if ! cmd`, $? es el de la negacion (siempre 0).
  # Hay que guardar el codigo antes de negarlo.
  local rc=0
  vence="$(normalizar_vencimiento "$pedido")" || rc=$?
  if [ "$rc" -ne 0 ]; then
    case "$rc" in
      3) echo "Esa fecha ya pasó: '$pedido'" >&2 ;;
      *) echo "Vencimiento inválido: '$pedido'. Usá 30d o 2026-12-31" >&2 ;;
    esac
    exit 1
  fi

  if grep -qE "^${nombre} " "$ARCHIVO_TOKENS"; then
    local t v
    t="$(awk -v n="$nombre" '$1==n {print $2}' "$ARCHIVO_TOKENS")"
    v="$(awk -v n="$nombre" '$1==n {print $4}' "$ARCHIVO_TOKENS")"
    echo "\"$nombre\" ya tenía acceso. Su link sigue siendo:" >&2
    url_de "$t"
    [[ -n "$v" ]] && echo "(vence el $v)" >&2
    echo "Cámara (VLC/mpv): $(url_cam_de "$t")" >&2
    return 0
  fi

  local t
  t="$(aleatorio)"
  printf '%s %s %s%s\n' "$nombre" "$t" "$(aleatorio)" "${vence:+ $vence}" >> "$ARCHIVO_TOKENS"
  regenerar_matcher
  echo "OK acceso dado a \"$nombre\""
  [[ -n "$vence" ]] && echo "   vence el $vence (ese día inclusive)"
  echo
  echo "  Pasale este link:"
  echo "  $(url_de "$t")"
  echo
  echo "  Al abrirlo en el navegador, el token se canjea por una cookie y"
  echo "  la pagina lo saca de la URL. Si el navegador no guarda la cookie,"
  echo "  el token sigue viajando en los pedidos de la pagina, pero nunca"
  echo "  mas en la direccion."
  echo
  echo "  Para VLC/mpv, el audio va con ese mismo link. La cámara no: los"
  echo "  segmentos de HLS no heredan el query, así que el token va en el"
  echo "  camino."
  echo "  $(url_cam_de "$t")"
  echo
  recargar_caddy
}

cmd_rm() {
  local nombre="${1:?uso: token.sh rm <nombre>}"
  validar_nombre "$nombre"
  if ! grep -qE "^${nombre} " "$ARCHIVO_TOKENS"; then
    echo "\"$nombre\" no estaba en la lista." >&2
    exit 1
  fi
  # El `|| true` no es decorativo: si es el ULTIMO token, grep -v no devuelve
  # lineas, sale con 1, y con `set -e` el script moria acá sin avisar.
  grep -vE "^${nombre} " "$ARCHIVO_TOKENS" > "$ARCHIVO_TOKENS.tmp" || true
  mv "$ARCHIVO_TOKENS.tmp" "$ARCHIVO_TOKENS"
  regenerar_matcher
  echo "OK acceso revocado a \"$nombre\" (token y cookie)"
  recargar_caddy
}

cmd_ls() {
  if ! [[ -s "$ARCHIVO_TOKENS" ]]; then
    echo "(nadie tiene acceso todavía)"
    return 0
  fi
  local hoy; hoy="$(date +%F)"
  printf '%-20s %-16s %s\n' "QUIEN" "TOKEN" "VENCE"
  while read -r n t c v; do
    [[ -z "${n:-}" ]] && continue
    if [[ -z "${v:-}" ]]; then
      printf '%-20s %-16s %s\n' "$n" "${t:0:12}..." "nunca"
    elif [[ "$v" < "$hoy" ]]; then
      printf '%-20s %-16s %s  <-- VENCIDO\n' "$n" "${t:0:12}..." "$v"
    else
      printf '%-20s %-16s %s\n' "$n" "${t:0:12}..." "$v"
    fi
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
  *) sed -n '2,/^set -euo/p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' | sed '$d'; exit 1 ;;
esac
