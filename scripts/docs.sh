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
HERRAMIENTAS="$RAIZ/.tools"
MMDC="$HERRAMIENTAS/node_modules/.bin/mmdc"
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
  echo "Instalando mermaid-cli en .tools/ (solo la primera vez)..."
  mkdir -p "$HERRAMIENTAS"
  [ -f "$HERRAMIENTAS/package.json" ] \
    || printf '{"name":"radio-docs","private":true}\n' > "$HERRAMIENTAS/package.json"
  if ! ( cd "$HERRAMIENTAS" && PUPPETEER_SKIP_DOWNLOAD=1 \
           npm install --no-audit --no-fund @mermaid-js/mermaid-cli >/dev/null 2>&1 ); then
    faltan "no se pudo instalar mermaid-cli (¿sin red?)"
  fi
  echo "Listo."
fi

# --------------------------------------------------------------- validacion

# Todos los .md, menos los que no nos interesan.
mapfile -t ARCHIVOS < <(
  find "$RAIZ" -name '*.md' -type f \
    -not -path '*/.git/*' -not -path '*/.tools/*' -not -path '*/node_modules/*' \
    -not -path '*/data/*' -not -path '*/data' -not -path '*/secrets/*' 2>/dev/null | sort
)

echo
echo "Validando diagramas Mermaid"
echo

cuenta=0; fallas=0

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
  printf '%s%d archivo(s) con diagramas rotos%s, de %d diagrama(s).\n' \
    "$ROJO" "$fallas" "$FIN" "$cuenta"
  echo "Arreglá el diagrama o mirá qué esperaba el parser en el mensaje de arriba."
  echo
  exit 1
fi
