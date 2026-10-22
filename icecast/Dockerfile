# Icecast construido desde Alpine.
#
# Por qué no usamos una imagen de Docker Hub: no hay una imagen de Icecast
# oficial y mantenida. Construir desde el paquete de Alpine es verificable,
# pesa ~15 MB y no depende de la buena voluntad de un tercero.

FROM alpine:3.21@sha256:bbfcd601d7cd20d35bb0b8b866ad1a8a0d107ca3e1c20870c75c5ce2c1186d30

# El paquete de Alpine ya crea el usuario y el grupo "icecast".
# mailcap aporta /etc/mime.types: sin él Icecast avisa que no puede
# reconocer tipos de archivo (ruido inofensivo, pero mejor sin él).
RUN apk add --no-cache icecast gettext mailcap \
 && mkdir -p /var/log/icecast /var/run/icecast \
 && chown -R icecast:icecast /var/log/icecast /var/run/icecast

COPY icecast.xml /etc/icecast.xml.tpl
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

USER icecast
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD wget -q -O- http://127.0.0.1:8000/status-json.xsl >/dev/null 2>&1 || exit 1

ENTRYPOINT ["/entrypoint.sh"]
