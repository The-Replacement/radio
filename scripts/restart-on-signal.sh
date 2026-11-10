#!/usr/bin/env bash
# Consume el signal de restart que escribe el generador al rotar la biblioteca.
#
# El generador corre con INDIEGEN_RESTART=signal:/var/lib/indiegen/restart-request
# (volumen ./data/indiegen). Al terminar un ciclo escribe ahí un timestamp; este
# script lo lee, y si cambió desde la última vez, hace `docker compose restart
# liquidsoap` — necesario porque el watch de liquidsoap suma tracks pero no
# olvida los borrados (los borrados "fantasma" seguirían sonando).
#
# Corre desde cron cada minuto, como el usuario del box (kbdejavu, en el grupo
# docker). No hay /var/run/docker.sock en ningún contenedor: el generador no
# puede tocar nada más que escribir un archivo; el reinicio es decisión de la
# radio, ejecutada por el host.
#
#   * * * * * /home/kbdejavu/servers/radio/scripts/restart-on-signal.sh
#
# Idempotente: si no hay signal nuevo, no toca nada. El marker se guarda al
# lado del signal (data/indiegen/.last-restart, gitignored).

set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SIGNAL="$ROOT/data/indiegen/restart-request"
MARK="$ROOT/data/indiegen/.last-restart"

[ -f "$SIGNAL" ] || exit 0

sig="$(cat "$SIGNAL" 2>/dev/null || true)"
last="$(cat "$MARK" 2>/dev/null || true)"

if [ -z "$sig" ] || [ "$sig" = "$last" ]; then
  exit 0
fi

docker compose -f "$ROOT/docker-compose.yml" restart liquidsoap
printf '%s' "$sig" > "$MARK"
