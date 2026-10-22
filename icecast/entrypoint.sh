#!/bin/sh
# Sustituye los placeholders del template por variables de entorno.
# Así los secretos no quedan escritos en icecast.xml dentro del repo.
set -eu

: "${ICECAST_SOURCE_PASSWORD:?falta ICECAST_SOURCE_PASSWORD}"
: "${ICECAST_RELAY_PASSWORD:?falta ICECAST_RELAY_PASSWORD}"
: "${ICECAST_ADMIN_PASSWORD:?falta ICECAST_ADMIN_PASSWORD}"

# Guarda contra el placeholder de .env.example: un deploy copiado sin
# completar arrancaría igual y quedaría con contraseñas adivinables.
# Son tres los que fallan juntos; no se arranca con ninguno débil.
debil() {
    # $1 = nombre de la variable, $2 = valor
    case "$2" in
        ""|cambiar_esto_por_uno_aleatorio|cambiar*|password*|secret*|contrasena*|123456*)
            echo "[icecast] $1 usa el placeholder o un valor débil. Cambialo en .env." >&2
            exit 1
            ;;
    esac
}
debil ICECAST_SOURCE_PASSWORD  "$ICECAST_SOURCE_PASSWORD"
debil ICECAST_RELAY_PASSWORD   "$ICECAST_RELAY_PASSWORD"
debil ICECAST_ADMIN_PASSWORD   "$ICECAST_ADMIN_PASSWORD"

envsubst < /etc/icecast.xml.tpl > /var/run/icecast/icecast.xml

echo "[icecast] config generada, arrancando en :8000 (solo red interna)"
exec icecast -c /var/run/icecast/icecast.xml
