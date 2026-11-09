#!/usr/bin/env bash
# smoke.sh — verifica que la radio esté arriba Y que la puerta cierre bien.
#
# Lo importante que prueba este script: que SIN credencial no se vea nada,
# y que el canje de token por cookie funcione.
# Una radio "privada" que se ve sin credencial no es privada.
#
# Hay DOS puertas y este script mide las dos: la del backend (BASE) y la de
# la página pública (FRONTEND, el origin del frontend). La segunda está
# abierta y medir una puerta abierta no es lo mismo que no medirla:
# ver grupo 0 (F-02).

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

# ----------------------------------------------- la segunda puerta (grupo 0)
#
# El backend cierra (el grupo de abajo); la PÁGINA no. Desde el split el
# player vive en Cloudflare Pages y este repo no tiene Pages, ni wrangler,
# ni Access, ni CI que lo gatee: nada impide servir la página entera sin
# credencial (F-02, issue #2, slice S5). Se mide igual, aunque duela, porque
# una radio privada cuyo cartel se lee sin credencial también es un problema
# — y una puerta que nadie mira no es una puerta.
#
# FRONTEND sale del .env (FRONTEND_URL): el origin del frontend. Local, es
# http://localhost:8090. Fallback: el .env no existe en esta máquina.
# Medido el 2026-11-09: 200, 32252 bytes, byte a byte idéntico a
# web/index.html al momento de la medida. stream.daza.ar, en cambio, 404 en
# todas sus rutas.
#
# EXCEPCIÓN DOCUMENTADA: la línea de abajo es lo único que autoriza un 200.
# Borrála y este gate vuelve a exigir 404 — con la página abierta queda
# rojo, que es la verdad. Si algún día la página cierra (404/403), borrá la
# excepción: una puerta que se cerró no deja autorización escrita atrás.
# Sin red el código es 000 y esto queda rojo a propósito: no se pudo medir,
# y un check que no midió no pasa en verde (la lección de F-07).
# Se lee FRONTEND_URL del .env y, si no está, cae a localhost. Lo único que
# se permite hardcodear es la excepción documentada de abajo; todo el resto
# del smoke mide con el origin del entorno (S8, F-06 / F-13).
FRONTEND="${FRONTEND:-$(grep -E '^FRONTEND_URL=' "$RAIZ/.env" 2>/dev/null | cut -d= -f2- || true)}"
FRONTEND="${FRONTEND:-http://localhost:8090}"
FRONTEND="${FRONTEND%/}"
EXCEPCION_F02=200   # F-02: la página se sirve sin puerta (medido 2026-11-09)

echo "la segunda puerta (la página, sin credencial):"
ff="$(codigo "$FRONTEND/")"
if [[ "$ff" == "404" || "$ff" == "403" ]]; then
  pasa "la página se cierra sin credencial ($ff)"
elif [[ -n "${EXCEPCION_F02+x}" && "$ff" == "$EXCEPCION_F02" ]]; then
  pasa "la página se sirve $ff sin credencial — puerta abierta, excepción documentada (F-02, #2)"
else
  falla "la página se sirve '$ff' sin credencial y sin excepción que la cubra (F-02): cerrá la puerta o escribí la excepción acá"
fi

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
    && pasa "y redirige al reproductor (302)" \
    || falla "no redirigió con 302"
  grep -qiE '^location: https://radio\.daza\.ar' <<<"$CABECERAS" \
    && pasa "la redirección apunta al frontend" \
    || falla "la redirección no apunta al frontend"
  # El 302 NO limpia la URL: conserva el ?t= a propósito, es el respaldo
  # para el navegador que no guarda cookies. De sacarlo de la barra se
  # encarga la página al cargar (S3/F-01). Si esta línea falla, ese
  # navegador pierde el respaldo y ni siquiera se entera.
  grep -qiF "location: $FRONTEND/?t=$TOKEN" <<<"$CABECERAS" \
    && pasa "y conserva el ?t= en la URL (el respaldo del navegador sin cookie)" \
    || falla "la redirección perdió el ?t=: el navegador sin cookie pierde el respaldo"

  # LA CADENA COMPLETA, no las mitades.
  #
  # Esto es lo único que prueba que el canje SIRVE: tomar la cookie que el
  # servidor acaba de entregar y usarla. Antes acá sólo se miraba que la
  # cabecera existiera y, por separado, que la cookie de tokens.txt
  # funcionara; el canje podía entregar cualquier basura y las pruebas
  # pasaban igual. Entregaba el token, el gate lo comparaba contra la
  # columna cookieid, y todo el mundo terminaba en 404.
  COOKIE_ENTREGADA="$(sed -nE 's/^[Ss]et-[Cc]ookie: radio=([^;]*).*/\1/p' <<<"$CABECERAS" | head -n1)"

  if [[ -z "$COOKIE_ENTREGADA" ]]; then
    falla "no pude leer la cookie que entregó el canje"
  else
    [[ "$(codigo -H "Cookie: radio=$COOKIE_ENTREGADA" "$BASE/")" == "200" ]] \
      && pasa "la cookie que ENTREGA el canje sirve el reproductor" \
      || falla "la cookie que ENTREGA el canje no sirve: ¿el canje copia el token en vez de traducirlo? (map token->cookieid en caddy/tokens.caddy)"
    [[ "$(codigo_stream -H "Cookie: radio=$COOKIE_ENTREGADA" "$BASE/stream")" == "200" ]] \
      && pasa "la cookie que ENTREGA el canje sirve el stream" \
      || falla "la cookie del canje no autenticó el stream"
  fi

  # Un token inválido no debe pisar una sesión válida con una cookie vacía.
  if [[ -n "$COOKIE" ]]; then
    [[ "$(codigo -b /dev/null -H "Cookie: radio=$COOKIE" "$BASE/?t=basura")" == "200" ]] \
      && pasa "un token inválido no rompe una sesión válida" \
      || falla "un token inválido pisó la cookie válida"
  fi

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

  # ------------------------------------------- el query, en lo que corre
  #
  # Esto reemplaza el grep viejo ('stream?t=' en los logs de Icecast), que
  # no podía fallar: Icecast corta el query en '?' antes de guardar el __uri
  # (Icecast-common/httpp.c) y el access log escribe ese campo tal cual
  # (Icecast-Server/src/logging.c) — una cadena que su formato no puede
  # contener, verde o rojo sin medir nada (F-07).
  #
  # Lo que sí se puede medir: la config que Caddy está sirviendo AHORA, por
  # su admin API (localhost:2019, sólo adentro del contenedor; el compose no
  # la publica). Si alguien saca el '?' del Caddyfile y no recarga —o
  # recarga y el cambio no entra—, el running config queda sin el rewrite y
  # este check se pone rojo. Medido en las dos direcciones contra el config
  # real: con S4 los tres greps dan, con el config de antes de S4 los tres
  # fallan.
  #
  # MediaMTX no tiene su propio grep: a logLevel: info (producción) no
  # anota ninguna línea de pedido (medido, con y sin query), así que un
  # grep en sus logs sería el mismo agujero que el de Icecast. Su rewrite
  # (/cam/whep?) queda cubierto por este mismo check. La prueba de
  # comportamiento —el '?' no cruza, mutación mediante— vive en
  # scripts/query-gate.sh.
  if docker compose -f "$RAIZ/docker-compose.yml" ps --status running --services 2>/dev/null | grep -qx caddy; then
    cfg="$(docker compose -f "$RAIZ/docker-compose.yml" exec -T caddy \
      wget -qO- http://127.0.0.1:2019/config/ 2>/dev/null || true)"
    if [[ -z "$cfg" ]]; then
      falla "no pude leer la config en ejecución de Caddy (admin API en 2019)"
    else
      for rw in '/stream?' '/status-json.xsl?' '/cam/whep?'; do
        grep -qF "\"rewrite\":{\"uri\":\"$rw\"}" <<<"$cfg" \
          && pasa "la config en ejecución tiene 'rewrite $rw'" \
          || falla "la config en ejecución NO tiene 'rewrite $rw' (¿no entró el reload?)"
      done
    fi
  fi

  # ---------------------------------------------------------- la cámara
  # Opcional: si no hay segmentos, no hay cámara que probar.
  #
  # Ojo con lo que se prueba acá. El playlist de HLS es un archivo que
  # apunta a otros archivos: una URL con ?t=... sirve el playlist pero NO
  # los segmentos, porque los reproductores piden las rutas relativas sin
  # el query. Por eso la cámara se prueba CON COOKIE. Para el navegador es
  # transparente (el canje ya dejó la cookie); un reproductor suelto como
  # VLC, en cambio, no puede ver la cámara sólo con el token.
  if [[ -f "$RAIZ/data/cam/index.m3u8" ]]; then
    echo
    echo "cámara (HLS):"

    [[ "$(codigo "$BASE/cam/index.m3u8")" == "404" ]] \
      && pasa "playlist sin credencial -> 404" \
      || falla "playlist sin credencial -> $(codigo "$BASE/cam/index.m3u8")"
    [[ "$(codigo "$BASE/cam/init.mp4")" == "404" ]] \
      && pasa "init sin credencial -> 404" \
      || falla "init sin credencial -> $(codigo "$BASE/cam/init.mp4")"

    if [[ -n "$COOKIE_ENTREGADA" ]]; then
      [[ "$(codigo -H "Cookie: radio=$COOKIE_ENTREGADA" "$BASE/cam/index.m3u8")" == "200" ]] \
        && pasa "playlist con cookie -> 200" \
        || falla "playlist con cookie -> $(codigo -H "Cookie: radio=$COOKIE_ENTREGADA" "$BASE/cam/index.m3u8")"

      # init.mp4 y no un segmento: los segmentos rotan (delete_segments) y
      # un nombre leído hace un segundo puede ya no existir. Los 404 por
      # rotación son ruido, no fallas.
      tipo="$(curl -s -o /dev/null -D - --max-time 5 -H "Cookie: radio=$COOKIE_ENTREGADA" \
              "$BASE/cam/init.mp4" 2>/dev/null | tr -d '\r' | awk 'tolower($1)=="content-type:" {print $2}')"
      [[ "$tipo" == "video/mp4" ]] \
        && pasa "init con cookie -> 200 ($tipo)" \
        || falla "init con cookie sirvió '$tipo' (esperaba video/mp4)"

      # Un segmento real, con un reintento por si rotó.
      seg="$(grep -v '^#' "$RAIZ/data/cam/index.m3u8" | grep -m1 . || true)"
      if [[ -n "$seg" ]]; then
        c="$(codigo -H "Cookie: radio=$COOKIE_ENTREGADA" "$BASE/cam/$seg")"
        if [[ "$c" != "200" ]]; then
          seg="$(grep -v '^#' "$RAIZ/data/cam/index.m3u8" | grep -m1 . || true)"
          c="$(codigo -H "Cookie: radio=$COOKIE_ENTREGADA" "$BASE/cam/$seg")"
        fi
        [[ "$c" == "200" ]] && pasa "segmento con cookie -> 200 ($seg)" \
                           || falla "segmento con cookie -> $c ($seg)"
      fi

      tipo="$(curl -s -o /dev/null -D - --max-time 5 -H "Cookie: radio=$COOKIE_ENTREGADA" \
              "$BASE/cam/${seg:-nada}" 2>/dev/null | tr -d '\r' | awk 'tolower($1)=="content-type:" {print $2}')"
      [[ "$tipo" == "video/iso.segment" ]] \
        && pasa "el segmento sale como $tipo" \
        || falla "el segmento salió como '$tipo' (esperaba video/iso.segment; ¿cambiaste a .ts?)"
    fi

    # --- WebRTC: el gate se aplica a la señalización ---
    # El SDP pasa por Caddy y por el gate; el video no (va por UDP directo).
    # Sin credencial, la señalización tiene que dar 404 como todo lo demás.
    [[ "$(codigo -X POST --data 'v=0' -H 'Content-Type: application/sdp' "$BASE/cam/whep")" == "404" ]] \
      && pasa "whep sin credencial -> 404" \
      || falla "whep sin credencial pasó el gate"
    if [[ -n "$COOKIE_ENTREGADA" ]]; then
      # 400 = llegó a mediamtx, que rechaza el SDP de prueba. 404 sería que
      # Caddy nunca lo ruteó. Se manda CON ?t=, que es la forma real del
      # fallback sin cookie (F-07): el rewrite tiene que borrarlo sin
      # romper el path — si el rewrite apuntara a otro lado, mediamtx
      # respondería 404 y esto se pondría rojo.
      [[ "$(codigo -X POST --data 'v=0' -H 'Content-Type: application/sdp' \
            -H "Cookie: radio=$COOKIE_ENTREGADA" "$BASE/cam/whep?t=$TOKEN")" == "400" ]] \
        && pasa "whep con credencial llega a mediamtx (400 = SDP inválido)" \
        || falla "whep con credencial no llegó a mediamtx"
    fi

    # --- el token en el CAMINO, que es como lo ven los reproductores ---
    # Con ?t=... baja el playlist y los segmentos dan 404: los pedidos
    # relativos no arrastran el query. Esto prueba la vía que sí funciona.
    echo
    echo "  token en el camino (VLC, mpv, ffmpeg):"

    [[ "$(codigo "$BASE/cam/$TOKEN/index.m3u8")" == "200" ]] \
      && pasa "playlist con token en el camino -> 200" \
      || falla "playlist con token en el camino -> $(codigo "$BASE/cam/$TOKEN/index.m3u8")"
    [[ "$(codigo "$BASE/cam/token-inventado/index.m3u8")" == "404" ]] \
      && pasa "token inventado en el camino -> 404" \
      || falla "token inventado pasó el gate"
    [[ "$(codigo "$BASE/cam/$TOKEN/..%2fweb%2findex.html")" == "404" ]] \
      && pasa "no se puede salir del directorio -> 404" \
      || falla "el path con .. no fue rechazado"

    if [[ -n "${seg:-}" ]]; then
      [[ "$(codigo "$BASE/cam/$TOKEN/$seg")" == "200" ]] \
        && pasa "el segmento relativo hereda el token -> 200 ($seg)" \
        || falla "el segmento relativo no heredó el token ($seg)"
    fi

    # La prueba que importa: un reproductor de verdad bajando todo.
    if command -v ffmpeg >/dev/null 2>&1; then
      if timeout 25 ffmpeg -hide_banner -v error -i "$BASE/cam/$TOKEN/index.m3u8" \
           -t 4 -f null - >/dev/null 2>&1; then
        pasa "ffmpeg baja y decodifica la cámara con el token"
      else
        falla "ffmpeg NO pudo bajar la cámara con el token"
      fi
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
