#!/usr/bin/env bash
# docs.sh - validates that the .md files' Mermaid diagrams render.
#
#   ./scripts/docs.sh
#
# A diagram with a syntax error is not noticed until someone opens the README.
# This catches it before that.
#
# Exit codes:
#   0  every diagram renders
#   1  at least one diagram is broken
#   2  missing dependencies (node, npm or a browser)
#
# The first time it installs mermaid-cli in .tools/ (it is in .gitignore).
# It uses the system's Chrome on purpose: otherwise it would download ~300 MB of
# Chromium.

set -uo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Validation dependencies. They go to the user's cache, NOT the repo:
# mermaid-cli takes ~450 MB and has no reason to live inside the project.
CACHE="${XDG_CACHE_HOME:-$HOME/.cache}/radio-docs"
MMDC="$CACHE/node_modules/.bin/mmdc"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

VERDE=$'\033[32m'; ROJO=$'\033[31m'; GRIS=$'\033[90m'; FIN=$'\033[0m'
INICIO="mermaid"

# ------------------------------------------------------------ dependencias

faltan() {
  printf '\n%sMISSING DEPENDENCIES%s\n  %s\n\n' "$ROJO" "$FIN" "$1" >&2
  exit 2
}

command -v node >/dev/null 2>&1 || faltan "'node' not found on PATH"
command -v npm  >/dev/null 2>&1 || faltan "'npm' not found on PATH"

NAVEGADOR=""
for b in google-chrome-stable google-chrome chromium chromium-browser; do
  if p="$(command -v "$b" 2>/dev/null)"; then NAVEGADOR="$p"; break; fi
done
[ -n "$NAVEGADOR" ] || faltan "mermaid needs a browser; neither Chrome nor Chromium was found"

if [ ! -x "$MMDC" ]; then
  echo "Installing mermaid-cli in $CACHE (first time only)..."
  mkdir -p "$CACHE"
  [ -f "$CACHE/package.json" ] \
    || printf '{"name":"radio-docs","private":true}\n' > "$CACHE/package.json"
  if ! ( cd "$CACHE" && PUPPETEER_SKIP_DOWNLOAD=1 \
           npm install --no-audit --no-fund @mermaid-js/mermaid-cli >/dev/null 2>&1 ); then
    faltan "could not install mermaid-cli (no network?)"
  fi
  echo "Done."
fi

# --------------------------------------------------------------- validation

# Every .md, except the ones we are not interested in.
mapfile -t ARCHIVOS < <(
  find "$RAIZ" -name '*.md' -type f \
    -not -path '*/.git/*' -not -path '*/node_modules/*' \
    -not -path '*/data/*' -not -path '*/data' -not -path '*/secrets/*' 2>/dev/null | sort
)

# ------------------------------------------ truth pass (F-12, F-14, F-20)
#
# What the docs claims and the code denies cannot come back. Three claims, three
# gates; any of them red makes docs.sh exit. The list grows with every "I
# documented this and nobody noticed".
#
echo
echo "Truth — what the docs claims and the code denies"
echo

trucos=0
fallas=0

# F-12: the cookie is host-only for stream.daza.ar — no place in the deployment
# tree can claim a Domain= that does not exist.
# (docs.sh excludes itself: the gate contains the claim by definition.)
if git grep -qF 'Domain=.daza.ar' -- caddy web README.md 2>/dev/null; then
  printf '  %sFAIL%s  "Domain=.daza.ar" appears — the cookie is host-only (F-12)\n' "$ROJO" "$FIN"
  trucos=$((trucos + 1))
else
  printf '  %sOK%s    no mention of Domain=.daza.ar in the tree\n' "$VERDE" "$FIN"
fi

# F-14: the vendor README presents hls.js loading as unusual; the code always loads it.
if grep -qE 'sólo si|only if' web/vendor/README.md 2>/dev/null; then
  printf '  %sFAIL%s  vendor README presents hls.js loading as conditional — the code always loads it (F-14)\n' "$ROJO" "$FIN"
  trucos=$((trucos + 1))
else
  printf '  %sOK%s    vendor README does not present hls.js loading as conditional\n' "$VERDE" "$FIN"
fi

# F-20: $CK is never used without being defined.
ck_block="$(awk '/^```/{f=!f; next} f && /CK/' README.md 2>/dev/null || true)"
if grep -qF '$CK' README.md 2>/dev/null && ! grep -qE '^CK=' <<<"$ck_block"; then
  printf '  %sFAIL%s  README.md uses $CK without defining CK= (F-20)\n' "$ROJO" "$FIN"
  trucos=$((trucos + 1))
else
  printf '  %sOK%s    every $CK appearance is preceded by CK= in the same block\n' "$VERDE" "$FIN"
fi

fallas=$((fallas + trucos))

echo
echo "Validating Mermaid diagrams"
echo

cuenta=0

for f in "${ARCHIVOS[@]}"; do
  rel="${f#"$RAIZ"/}"
  # Count ```mermaid blocks in a simple way.
  n="$(grep -c '^[[:space:]]*```mermaid' "$f" 2>/dev/null || true)"
  n="${n:-0}"
  [ "$n" -eq 0 ] && continue

  salida="$(PUPPETEER_EXECUTABLE_PATH="$NAVEGADOR" \
              "$MMDC" -i "$f" -o "$TMP/salida.png" -q 2>&1)"

  if [ $? -eq 0 ]; then
    printf '  %sOK%s    %-22s %s diagram(s)\n' "$VERDE" "$FIN" "$rel" "$n"
    cuenta=$((cuenta + n))
  else
    printf '  %sFAIL%s %-22s\n' "$ROJO" "$FIN" "$rel"
    # From Node's dump only the block from "Error:" to the stack is useful.
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
    printf '%sThere are no Mermaid diagrams in the repo.%s\n\n' "$GRIS" "$FIN"
  else
    printf '%sAll good%s - %d diagram(s) render.\n\n' "$VERDE" "$FIN" "$cuenta"
  fi
  exit 0
else
  printf '%s%d check(s) fail%s\n' "$ROJO" "$fallas" "$FIN"
  echo "Fix the diagram or the claims the gate just caught."
  echo
  exit 1
fi
