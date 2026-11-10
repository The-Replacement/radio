#!/usr/bin/env bash
# query-gate.sh — the token's query NEVER crosses the door (F-07, slice S4).
#
# The old promise ("rewrite drops the query") rested on two things that could
# not fail:
#
#   1. Caddy does NOT drop the query with `rewrite /stream`: it only changes the
#      path and PRESERVES the query (modules/caddyhttp/rewrite/rewrite.go,
#      URI field: "a value of /foo.html will only change the path and will
#      preserve any existing query string"). What clears it is the empty '?':
#      "To clear the query string, explicitly set an empty one". Without that
#      '?', ?t= reached Icecast and mediamtx on every request.
#   2. smoke's grep looked for 'stream?t=' in Icecast's logs, but Icecast cuts
#      the query at the '?' before storing __uri: a string its format cannot
#      contain — green or red without measuring anything.
#
# This gate measures the config instead of trusting it:
#
#   - it extracts the three `handle` blocks from the Caddyfile that is deployed,
#     exactly as they are: only the upstream address is swapped for a local
#     origin's. If the rewrite is not INSIDE the block (or the format changed),
#     the extraction returns nothing and the gate fails loudly;
#   - it starts a tiny origin that records the request line it receives;
#   - it runs the pinned Caddy from docker-compose.yml against it and demands
#     that NO '?' arrives on /stream, /status-json.xsl and /cam/whep;
#   - it mutates each block (removes the '?' from its rewrite — the config as it
#     was before S4) and demands that the leak APPEARS. A mutation that stays
#     clean means the gate stopped measuring that route: that is a red, not a
#     green.
#
# It runs with no stack and no .env: it needs docker (the daemon) and python3.
# CADDYFILE=/other/Caddyfile points the gate at another version of the file —
# that is how the previous one is shown red: git show <rev>:caddy/Caddyfile.
#
# Exit codes:
#   0  the three routes clean AND the three mutations caught
#   1  something measured something other than what it claimed to measure
#   2  missing dependencies (docker, python3, the caddy image)

set -uo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CADDYFILE="${CADDYFILE:-$RAIZ/caddy/Caddyfile}"
COMPOSE="$RAIZ/docker-compose.yml"
TMP="$(mktemp -d)"
NOMBRE="radio-query-gate-$$"
ORIGEN_PID=""

ok=0; mal=0
pasa()  { printf '  \033[32mOK\033[0m %s\n' "$1"; ok=$((ok+1)); }
falla() { printf '  \033[31mFAIL\033[0m %s\n' "$1"; mal=$((mal+1)); }

resumen() {
  echo
  if [ "$mal" -eq 0 ]; then
    printf '\033[32mAll good\033[0m — %d checks.\n\n' "$ok"
    exit 0
  fi
  printf '\033[31m%d failures\033[0m out of %d checks.\n' "$mal" "$((ok+mal))"
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
  printf '\n\033[31mMISSING DEPENDENCIES\033[0m\n  %s\n\n' "$1" >&2
  exit 2
}

# ------------------------------------------------------------ dependencias

command -v python3 >/dev/null 2>&1 || faltan "'python3' not found on PATH"
command -v docker  >/dev/null 2>&1 || faltan "'docker' not found on PATH"
docker info >/dev/null 2>&1        || faltan "the docker daemon does not answer"
[ -f "$CADDYFILE" ]                || faltan "$CADDYFILE not found"

# The image pinned in docker-compose.yml: the same one production runs, not a
# "latest" one just in case.
IMG="$(awk '/image:[[:space:]]*caddy:/ {print $2; exit}' "$COMPOSE")"
[ -n "$IMG" ] || faltan "the caddy image was not found in $COMPOSE"
if ! docker image inspect "$IMG" >/dev/null 2>&1; then
  echo "Pulling $IMG (first time only)..."
  docker pull "$IMG" >/dev/null 2>&1 || faltan "could not pull $IMG"
fi

# ------------------------------------------- extracting the blocks from the file

# The COMPLETE handle block, exactly as it is in the file that is deployed:
# afterwards only the upstream address is changed. If the rewrite lives outside
# the block, or the file's format changed, this returns nothing and the gate
# fails loudly instead of measuring an idealized copy.
bloque() { # $1 = the exact line that opens the block, e.g. 'handle /stream* {'
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

# The block's rewrite line (on stdin), exactly one. It returns the complete pair
# ("rewrite /stream?"), which is what gets inserted into the test config: the
# line that runs in production, not a reimplementation.
rewrite_de() { # $1 = path, e.g. /stream
  awk -v r="$1?" '
    $1 == "rewrite" && $2 == r && NF == 2 { n++; l = $1 " " $2 }
    END { if (n == 1) print l }
  '
}

mutar() { # stdin: block; $1 = path — removes the '?' from the rewrite (pre-S4)
  sed "s|rewrite $1?|rewrite $1|"
}

localizar() { # the file's upstream -> this gate's origin
  sed -E "s|reverse_proxy[[:space:]]+[^ {]+|reverse_proxy 127.0.0.1:$ORIGEN|g"
}

# ----------------------------------------------------------------- origen

# An origin that records the request line it receives (method + path + query).
# If a '?' shows up here, the query crossed the door.
cat > "$TMP/origen.py" <<'PY'
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

LOG = sys.argv[2]

class Origin(BaseHTTPRequestHandler):
    def _atender(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n:
            self.rfile.read(n)  # consume the body (whep's SDP)
        with open(LOG, "a") as f:
            f.write(f"{self.command} {self.path}\n")  # path = path + query
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    do_GET = do_POST = _atender

    def log_message(self, *args):
        pass  # what matters is in the file, not on stderr

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

config() { # $1 $2 $3 = the three blocks, already with a local upstream
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
  # --network host: on Linux the container shares the host's loopback, so it
  # sees the origin on 127.0.0.1 and the gate listens on the same port.
  for _ in $(seq 1 50); do
    curl -s -o /dev/null --max-time 1 "http://127.0.0.1:$PUERTA/" 2>/dev/null && return 0
    sleep 0.1
  done
  return 1
}

diagnostico() {
  docker logs "$NOMBRE" 2>&1 | tail -n 5 | sed 's/^/        /'
}

pedir() { # $1 = path; the fake token travels in the query, as in real life
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
echo "Query gate — the query does not cross the door (F-07)"
echo

BLOQUES=(); roto=0
for i in 0 1 2; do
  b="$(bloque "${APERTURAS[$i]}")"
  if [ -z "$b" ]; then
    falla "did not find '${APERTURAS[$i]}' in $CADDYFILE — without that block there is nothing to measure"
    roto=1; BLOQUES+=(""); continue
  fi
  r="$(printf '%s\n' "$b" | rewrite_de "${RUTAS[$i]}")"
  if [ -z "$r" ]; then
    falla "the ${RUTAS[$i]} block does not contain exactly one 'rewrite ${RUTAS[$i]}?' — without that '?' the query crosses"
    roto=1; BLOQUES+=(""); continue
  fi
  pasa "Caddyfile: ${RUTAS[$i]} has '$r' inside its block"
  BLOQUES+=("$b")
done
[ "$roto" -eq 0 ] || resumen

# ------------------------------------------------------------ con el '?'

LOCAL=()
for i in 0 1 2; do LOCAL+=("$(printf '%s\n' "${BLOQUES[$i]}" | localizar)"); done

config "${LOCAL[0]}" "${LOCAL[1]}" "${LOCAL[2]}"
if ! arrancar; then
  falla "caddy did not start with the test config"
  diagnostico
  resumen
fi

: > "$TMP/pedidos.log"
for i in 0 1 2; do pedir "${RUTAS[$i]}"; done

for i in 0 1 2; do
  if grep -qFx "${SIN_QUERY[$i]}" "$TMP/pedidos.log"; then
    pasa "${RUTAS[$i]}: the origin received '${SIN_QUERY[$i]}' — no '?', no token"
  else
    llego="$(grep -F "${RUTAS[$i]}" "$TMP/pedidos.log" | tail -n1)"
    falla "${RUTAS[$i]}: the origin received '${llego:-nothing}' — the query crossed"
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
    falla "mutation ${RUTAS[$i]}: caddy did not start with the broken config"
    diagnostico
    continue
  fi

  : > "$TMP/pedidos.log"
  pedir "${RUTAS[$i]}"

  if grep -qFx "${CON_QUERY[$i]}" "$TMP/pedidos.log"; then
    pasa "mutation ${RUTAS[$i]}: without the '?', the query arrived ('${CON_QUERY[$i]}') — this gate can fail"
  else
    llego="$(grep -F "${RUTAS[$i]}" "$TMP/pedidos.log" | tail -n1)"
    falla "mutation ${RUTAS[$i]}: I removed the '?' but the query did NOT arrive ('${llego:-nothing}') — this gate stopped measuring ${RUTAS[$i]}"
  fi
done

resumen
