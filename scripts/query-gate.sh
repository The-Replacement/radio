#!/usr/bin/env bash
# query-gate.sh — el query del token NUNCA cruza la puerta (F-07, slice S4).
#
# La promesa vieja ("rewrite descarta el query") se sostenía en dos cosas
# que no podían fallar:
#
#   1. Caddy NO descarta el query con `rewrite /stream`: sólo cambia el
#      path y CONSERVA el query (modules/caddyhttp/rewrite/rewrite.go,
#      campo URI: "a value of /foo.html will only change the path and will
#      preserve any existing query string"). Lo que lo borra es el '?'
#      vacío: "To clear the query string, explicitly set an empty one".
#      Sin ese '?', ?t= llegaba a Icecast y a mediamtx en cada pedido.
#   2. El grep de smoke buscaba 'stream?t=' en los logs de Icecast, pero
#      Icecast corta el query en '?' antes de guardar el __uri: una cadena
#      que su formato no puede contener — verde o rojo sin medir nada.
#
# Este gate mide la config en vez de esperarla:
#
#   - extrae los tres bloques `handle` del Caddyfile que se despliega,
#     tal cual: sólo se cambia la dirección del upstream por la de un
#     origin local. Si el rewrite no está DENTRO del bloque (o el formato
#     cambió), la extracción no devuelve nada y el gate falla ruidoso;
#   - levanta un origin chiquito que anota la línea de pedido que recibe;
#   - corre el Caddy pineado de docker-compose.yml contra él y exige que
#     NO llegue ningún '?' en /stream, /status-json.xsl y /cam/whep;
#   - muta cada bloque (le saca el '?' a su rewrite — el config tal como
#     estaba antes de S4) y exige que la filtración APAREZCA. Una mutación
#     que queda limpia significa que el gate dejó de medir esa ruta: eso
#     es un rojo, no un verde.
#
# Corre sin stack y sin .env: necesita docker (el daemon) y python3.
# CADDYFILE=/otro/Caddyfile apunta el gate a otra versión del archivo —
# así se le muestra rojo al anterior: git show <rev>:caddy/Caddyfile.
#
# Códigos de salida:
#   0  las tres rutas limpias Y las tres mutaciones atrapadas
#   1  algo midió otra cosa de lo que decía medir
#   2  faltan dependencias (docker, python3, la imagen de caddy)

set -uo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CADDYFILE="${CADDYFILE:-$RAIZ/caddy/Caddyfile}"
COMPOSE="$RAIZ/docker-compose.yml"
TMP="$(mktemp -d)"
NOMBRE="radio-query-gate-$$"
ORIGEN_PID=""

ok=0; mal=0
pasa()  { printf '  \033[32mOK\033[0m %s\n' "$1"; ok=$((ok+1)); }
falla() { printf '  \033[31mFALLA\033[0m %s\n' "$1"; mal=$((mal+1)); }

resumen() {
  echo
  if [ "$mal" -eq 0 ]; then
    printf '\033[32mTodo bien\033[0m — %d chequeos.\n\n' "$ok"
    exit 0
  fi
  printf '\033[31m%d fallas\033[0m de %d chequeos.\n' "$mal" "$((ok+mal))"
  echo
  exit 1
}

limpiar() {
  if [ -n "$ORIGEN_PID" ]; then kill "$ORIGEN_PID" 2>/dev/null; fi
  docker rm -f "$NOMBRE" >/dev/null 2>&1
  rm -rf "$TMP"
}
trap limpiar EXIT

faltan() {
  printf '\n\033[31mFALTAN DEPENDENCIAS\033[0m\n  %s\n\n' "$1" >&2
  exit 2
}

# ------------------------------------------------------------ dependencias

command -v python3 >/dev/null 2>&1 || faltan "no encontre 'python3' en el PATH"
command -v docker  >/dev/null 2>&1 || faltan "no encontre 'docker' en el PATH"
docker info >/dev/null 2>&1        || faltan "el demonio de docker no responde"
[ -f "$CADDYFILE" ]                || faltan "no encontre $CADDYFILE"

# La imagen pineada en docker-compose.yml: la misma que corre en producción,
# ni una de "latest" por las dudas.
IMG="$(awk '/image:[[:space:]]*caddy:/ {print $2; exit}' "$COMPOSE")"
[ -n "$IMG" ] || faltan "no encontre la imagen de caddy en $COMPOSE"
if ! docker image inspect "$IMG" >/dev/null 2>&1; then
  echo "Bajando $IMG (solo la primera vez)..."
  docker pull "$IMG" >/dev/null 2>&1 || faltan "no pude bajar $IMG"
fi

# ------------------------------------------- sacar los bloques del archivo

# El bloque handle COMPLETO, tal cual está en el archivo que se despliega:
# después sólo se le cambia la dirección del upstream. Si el rewrite vive
# fuera del bloque, o el formato del archivo cambió, esto no devuelve nada
# y el gate falla ruidoso en vez de medir una copia idealizada.
bloque() { # $1 = linea exacta que abre el bloque, ej: 'handle /stream* {'
  awk -v apertura="$1" '
    !dentro {
      linea = $0
      sub(/^[[:space:]]*/, "", linea)
      if (linea == apertura) { dentro = 1 } else { next }
    }
    dentro {
      print
      for (i = 1; i <= length($0); i++) {
        c = substr($0, i, 1)
        if (c == "{") prof++
        else if (c == "}") prof--
      }
      if (prof == 0) exit
    }
  ' "$CADDYFILE"
}

# La línea de rewrite del bloque (por stdin), exactamente una. Devuelve el
# par completo ("rewrite /stream?"), que es lo que se inserta en el config
# de prueba: la línea que corre en producción, no una reimplementación.
rewrite_de() { # $1 = ruta, ej: /stream
  awk -v r="$1?" '
    $1 == "rewrite" && $2 == r && NF == 2 { n++; l = $1 " " $2 }
    END { if (n == 1) print l }
  '
}

mutar() { # stdin: bloque; $1 = ruta — le saca el '?' al rewrite (pre-S4)
  sed "s|rewrite $1?|rewrite $1|"
}

localizar() { # el upstream del archivo -> el origin de este gate
  sed -E "s|reverse_proxy[[:space:]]+[^ {]+|reverse_proxy 127.0.0.1:$ORIGEN|g"
}

# ----------------------------------------------------------------- origen

# Un origin que anota la línea de pedido que recibe (method + path + query).
# Si acá aparece un '?', es que el query cruzó la puerta.
cat > "$TMP/origen.py" <<'PY'
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

LOG = sys.argv[2]

class Origin(BaseHTTPRequestHandler):
    def _atender(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n:
            self.rfile.read(n)  # consume el body (el SDP del whep)
        with open(LOG, "a") as f:
            f.write(f"{self.command} {self.path}\n")  # path = path + query
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    do_GET = do_POST = _atender

    def log_message(self, *args):
        pass  # lo que importa está en el archivo, no en stderr

HTTPServer(("127.0.0.1", int(sys.argv[1])), Origin).serve_forever()
PY

puerto_libre() {
  python3 -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0))
print(s.getsockname()[1]); s.close()'
}
PUERTA="$(puerto_libre)"
ORIGEN="$(puerto_libre)"

python3 "$TMP/origen.py" "$ORIGEN" "$TMP/pedidos.log" &
ORIGEN_PID=$!
for _ in $(seq 1 50); do
  if (exec 3<>"/dev/tcp/127.0.0.1/$ORIGEN") 2>/dev/null; then exec 3>&-; break; fi
  sleep 0.1
done

# ------------------------------------------------------------------ caddy

config() { # $1 $2 $3 = los tres bloques, ya con upstream local
  {
    printf '{\n\tauto_https off\n\tadmin off\n}\n\n'
    printf 'http://127.0.0.1:%s {\n' "$PUERTA"
    printf '%s\n%s\n%s\n' "$1" "$2" "$3"
    printf '\thandle {\n\t\trespond 404\n\t}\n'
    printf '}\n'
  } > "$TMP/Caddyfile"
}

arrancar() {
  docker rm -f "$NOMBRE" >/dev/null 2>&1
  docker run -d --name "$NOMBRE" --network host \
    -v "$TMP/Caddyfile:/etc/caddy/Caddyfile:ro" \
    "$IMG" caddy run --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null 2>&1 \
    || return 1
  # --network host: en Linux el contenedor comparte el loopback del host,
  # así ve al origin en 127.0.0.1 y el gate lo escucha en el mismo puerto.
  for _ in $(seq 1 50); do
    curl -s -o /dev/null --max-time 1 "http://127.0.0.1:$PUERTA/" 2>/dev/null && return 0
    sleep 0.1
  done
  return 1
}

diagnostico() {
  docker logs "$NOMBRE" 2>&1 | tail -n 5 | sed 's/^/        /'
}

pedir() { # $1 = ruta; el token de mentira viaja en el query, como en la vida real
  if [ "$1" = /cam/whep ]; then
    curl -s -o /dev/null --max-time 5 -X POST --data 'v=0' \
      -H 'Content-Type: application/sdp' \
      "http://127.0.0.1:$PUERTA$1?t=SECRETO" 2>/dev/null || true
  else
    curl -s -o /dev/null --max-time 5 \
      "http://127.0.0.1:$PUERTA$1?t=SECRETO" 2>/dev/null || true
  fi
}

# ------------------------------------------------------------- estructura

RUTAS=(/stream /status-json.xsl /cam/whep)
APERTURAS=("handle /stream* {" "handle /status-json.xsl {" "handle /cam/whep {")
SIN_QUERY=("GET /stream" "GET /status-json.xsl" "POST /cam/whep")
CON_QUERY=("GET /stream?t=SECRETO" "GET /status-json.xsl?t=SECRETO" "POST /cam/whep?t=SECRETO")

echo
echo "Query gate — el query no cruza la puerta (F-07)"
echo

BLOQUES=(); roto=0
for i in 0 1 2; do
  b="$(bloque "${APERTURAS[$i]}")"
  if [ -z "$b" ]; then
    falla "no encontre '${APERTURAS[$i]}' en $CADDYFILE — sin ese bloque no hay nada que medir"
    roto=1; BLOQUES+=(""); continue
  fi
  r="$(printf '%s\n' "$b" | rewrite_de "${RUTAS[$i]}")"
  if [ -z "$r" ]; then
    falla "el bloque ${RUTAS[$i]} no contiene una unica 'rewrite ${RUTAS[$i]}?' — sin ese '?' el query cruza"
    roto=1; BLOQUES+=(""); continue
  fi
  pasa "Caddyfile: ${RUTAS[$i]} tiene '$r' dentro de su bloque"
  BLOQUES+=("$b")
done
[ "$roto" -eq 0 ] || resumen

# ------------------------------------------------------------ con el '?'

LOCAL=()
for i in 0 1 2; do LOCAL+=("$(printf '%s\n' "${BLOQUES[$i]}" | localizar)"); done

config "${LOCAL[0]}" "${LOCAL[1]}" "${LOCAL[2]}"
if ! arrancar; then
  falla "caddy no arranco con el config de prueba"
  diagnostico
  resumen
fi

: > "$TMP/pedidos.log"
for i in 0 1 2; do pedir "${RUTAS[$i]}"; done

for i in 0 1 2; do
  if grep -qFx "${SIN_QUERY[$i]}" "$TMP/pedidos.log"; then
    pasa "${RUTAS[$i]}: el origin recibio '${SIN_QUERY[$i]}' — sin '?', sin token"
  else
    llego="$(grep -F "${RUTAS[$i]}" "$TMP/pedidos.log" | tail -n1)"
    falla "${RUTAS[$i]}: el origin recibio '${llego:-nada}' — el query cruzo"
  fi
done
[ "$mal" -eq 0 ] || resumen

# --------------------------------------------------- mutaciones (pre-S4)

for i in 0 1 2; do
  mut=()
  for j in 0 1 2; do
    if [ "$j" -eq "$i" ]; then
      mut+=("$(printf '%s\n' "${BLOQUES[$j]}" | mutar "${RUTAS[$j]}")")
    else
      mut+=("${BLOQUES[$j]}")
    fi
  done

  config "$(printf '%s\n' "${mut[0]}" | localizar)" \
         "$(printf '%s\n' "${mut[1]}" | localizar)" \
         "$(printf '%s\n' "${mut[2]}" | localizar)"
  if ! arrancar; then
    falla "mutacion ${RUTAS[$i]}: caddy no arranco con el config roto"
    diagnostico
    continue
  fi

  : > "$TMP/pedidos.log"
  pedir "${RUTAS[$i]}"

  if grep -qFx "${CON_QUERY[$i]}" "$TMP/pedidos.log"; then
    pasa "mutacion ${RUTAS[$i]}: sin el '?', el query llego ('${CON_QUERY[$i]}') — este gate puede fallar"
  else
    llego="$(grep -F "${RUTAS[$i]}" "$TMP/pedidos.log" | tail -n1)"
    falla "mutacion ${RUTAS[$i]}: saque el '?' pero el query NO llego ('${llego:-nada}') — este gate dejo de medir ${RUTAS[$i]}"
  fi
done

resumen
