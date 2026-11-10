#!/bin/sh
# Replaces the template's placeholders with environment variables.
# That way the secrets do not end up written in icecast.xml inside the repo.
set -eu

: "${ICECAST_SOURCE_PASSWORD:?ICECAST_SOURCE_PASSWORD is missing}"
: "${ICECAST_RELAY_PASSWORD:?ICECAST_RELAY_PASSWORD is missing}"
: "${ICECAST_ADMIN_PASSWORD:?ICECAST_ADMIN_PASSWORD is missing}"

# Guard against .env.example's placeholder: a deployment copied without filling
# it in would start anyway and end up with guessable passwords.
# Three of them fail together; it does not start with any weak one.
debil() {
    # $1 = variable name, $2 = value
    case "$2" in
        ""|change_this_for_a_random_one|change_this*|password*|secret*|contrasena*|123456*)
            echo "[icecast] $1 uses the placeholder or a weak value. Change it in .env." >&2
            exit 1
            ;;
    esac
}
debil ICECAST_SOURCE_PASSWORD  "$ICECAST_SOURCE_PASSWORD"
debil ICECAST_RELAY_PASSWORD   "$ICECAST_RELAY_PASSWORD"
debil ICECAST_ADMIN_PASSWORD   "$ICECAST_ADMIN_PASSWORD"

envsubst < /etc/icecast.xml.tpl > /var/run/icecast/icecast.xml

echo "[icecast] config generated, starting on :8000 (internal network only)"
exec icecast -c /var/run/icecast/icecast.xml
