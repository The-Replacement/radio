#!/bin/sh
# Sustituye los placeholders del template por variables de entorno.
# Así los secretos no quedan escritos en icecast.xml dentro del repo.
set -eu

: "${ICECAST_SOURCE_PASSWORD:?falta ICECAST_SOURCE_PASSWORD}"
: "${ICECAST_RELAY_PASSWORD:?falta ICECAST_RELAY_PASSWORD}"
: "${ICECAST_ADMIN_PASSWORD:?falta ICECAST_ADMIN_PASSWORD}"

envsubst < /etc/icecast.xml.tpl > /var/run/icecast/icecast.xml

echo "[icecast] config generada, arrancando en :8000 (solo red interna)"
exec icecast -c /var/run/icecast/icecast.xml
