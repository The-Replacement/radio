#!/usr/bin/env bash
# docs.sh - valida que los diagramas Mermaid de los .md rendericen.
#
#   ./scripts/docs.sh
#
# Un diagrama con error de sintaxis no se nota hasta que alguien abre el
# README. Esto lo agarra antes.
#
# Codigos de salida:
#   0  todos los diagramas renderizan
#   1  hay al menos un diagrama roto
#   2  faltan dependencias (node, npm o un navegador)
#
# La primera vez instala mermaid-cli en .tools/ (esta en .gitignore).
# Usa el Chrome del sistema a proposito: si no, bajaría ~300 MB de Chromium.

set -uo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Dependencias de validacion. Van al cache del usuario, NO al repo:
# mermaid-cli ocupa ~450 MB y no tiene por que vivir dentro del proyecto.
CACHE="${XDG_CACHE_HOME:-$HOME/.cache}/radio-docs"
MMDC="$CACHE/node_modules/.bin/mmdc"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

VERDE=$'\033[32m'; ROJO=$'\033[31m'; GRIS=$'\033[90m'; FIN=$'\033[0m'
INICIO="mermaid"

# ------------------------------------------------------------ dependencias

faltan() {
  printf '\n%sFALTAN DEPENDENCIAS%s\n  %s\n\n' "$ROJO" "$FIN" "$1" >&2
  exit 2
}

command -v node >/dev/null 2>&1 || faltan "no encontre 'node' en el PATH"
command -v npm  >/dev/null 2>&1 || faltan "no encontre 'npm' en el PATH"

NAVEGADOR=""
for b in google-chrome-stable google-chrome chromium chromium-browser; do
  if p="$(command -v "$b" 2>/dev/null)"; then NAVEGADOR="$p"; break; fi
done
[ -n "$NAVEGADOR" ] || faltan "mermaid necesita un navegador; no encontre Chrome ni Chromium"

if [ ! -x "$MMDC" ]; then
  echo "Instalando mermaid-cli en $CACHE (solo la primera vez)..."
  mkdir -p "$CACHE"
  [ -f "$CACHE/package.json" ] \
    || printf '{"name":"radio-docs","private":true}\n' > "$CACHE/package.json"
  if ! ( cd "$CACHE" && PUPPETEER_SKIP_DOWNLOAD=1 \
           npm install --no-audit --no-fund @mermaid-js/mermaid-cli >/dev/null 2>&1 ); then
    faltan "no se pudo instalar mermaid-cli (¿sin red?)"
  fi
  echo "Listo."
fi

# --------------------------------------------------------------- validacion

# Todos los .md, menos los que no nos interesan.
mapfile -t ARCHIVOS < <(
  find "$RAIZ" -name '*.md' -type f \
    -not -path '*/.git/*' -not -path '*/node_modules/*' \
    -not -path '*/data/*' -not -path '*/data' -not -path '*/secrets/*' 2>/dev/null | sort
)

# ------------------------------------------ truth pass (F-12, F-14, F-20)
#
# Lo que el docs afirma y el código desmiente no puede volver. Tres
# afirmaciones, tres gates; cualquiera de ellas rojo hace salir docs.sh.
# La lista crece con cada "documenté esto y nadie lo notó".
#
echo
echo "Truth — lo que el docs afirma y el código desmiente"
echo

trucos=0
fallas=0

# F-12: la cookie está host-only para stream.daza.ar — ningún sitio del
# árbol del despliegue puede afirmar un Domain= que no existe.
# (docs.sh se excluye: el gate contiene la afirmación por definición.)
if git grep -qF 'Domain=.daza.ar' -- caddy web README.md 2>/dev/null; then
  printf '  %sFALLA%s  "Domain=.daza.ar" aparece — la cookie es host-only (F-12)\n' "$ROJO" "$FIN"
  trucos=$((trucos + 1))
else
  printf '  %sOK%s    ninguna mención a Domain=.daza.ar en el árbol\n' "$VERDE" "$FIN"
fi

# F-14: el vendor README presenta la carga de hls.js como rara; el código la carga siempre.
if grep -qE 'sólo si|only if' web/vendor/README.md 2>/dev/null; then
  printf '  %sFALLA%s  vendor README presenta la carga de hls.js como condicional — el código la carga siempre (F-14)\n' "$ROJO" "$FIN"
  trucos=$((trucos + 1))
else
  printf '  %sOK%s    vendor README no presenta la carga de hls.js como condicional\n' "$VERDE" "$FIN"
fi

# F-20: $CK no se usa nunca sin definir.
ck_block="$(awk '/^```/{f=!f; next} f && /CK/' README.md 2>/dev/null || true)"
if grep -qF '$CK' README.md 2>/dev/null && ! grep -qE '^CK=' <<<"$ck_block"; then
  printf '  %sFALLA%s  README.md usa $CK sin definir CK= (F-20)\n' "$ROJO" "$FIN"
  trucos=$((trucos + 1))
else
  printf '  %sOK%s    toda aparición de $CK está precedida por CK= en el mismo bloque\n' "$VERDE" "$FIN"
fi

fallas=$((fallas + trucos))

echo
echo "Validando diagramas Mermaid"
echo

cuenta=0

for f in "${ARCHIVOS[@]}"; do
  rel="${f#"$RAIZ"/}"
  # Contar bloques ```mermaid de forma simple.
  n="$(grep -c '^[[:space:]]*```mermaid' "$f" 2>/dev/null || true)"
  n="${n:-0}"
  [ "$n" -eq 0 ] && continue

  salida="$(PUPPETEER_EXECUTABLE_PATH="$NAVEGADOR" \
              "$MMDC" -i "$f" -o "$TMP/salida.png" -q 2>&1)"

  if [ $? -eq 0 ]; then
    printf '  %sOK%s    %-22s %s diagrama(s)\n' "$VERDE" "$FIN" "$rel" "$n"
    cuenta=$((cuenta + n))
  else
    printf '  %sFALLA%s %-22s\n' "$ROJO" "$FIN" "$rel"
    # Del volcado de Node solo sirve el bloque desde "Error:" hasta el stack.
    mensaje="$(printf '%s\n' "$salida" | sed -n '/^Error:/,/^Parser\./p' | sed '$d')"
    if [ -z "$mensaje" ]; then
      mensaje="$(printf '%s\n' "$salida" | grep -iE 'error|parse' | head -4)"
    fi
    printf '%s\n' "$mensaje" | sed 's/^/        /'
    fallas=$((fallas + 1))
  fi
done

echo
if [ "$fallas" -eq 0 ]; then
  if [ "$cuenta" -eq 0 ]; then
    printf '%sNo hay diagramas Mermaid en el repo.%s\n\n' "$GRIS" "$FIN"
  else
    printf '%sTodo bien%s - %d diagrama(s) renderizan.\n\n' "$VERDE" "$FIN" "$cuenta"
  fi
  exit 0
else
  printf '%s%d check(s) fallan%s\n' "$ROJO" "$fallas" "$FIN"
  echo "Arreglá el diagrama o las afirmaciones del docs que el gate acaba de cazar."
  echo
  exit 1
fi
