#!/bin/sh
# Dispara un ciclo inmediato del generador (sirve como trigger en post-receive
# o manualmente). El contenedor indiegen vigila /var/lib/indiegen/trigger.
set -eu
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TRIGGER="$ROOT/data/indiegen/trigger"
mkdir -p "$(dirname "$TRIGGER")"
date -u +%Y-%m-%dT%H:%M:%SZ > "$TRIGGER"
echo "trigger escrito: $TRIGGER"