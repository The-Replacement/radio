# La cámara: ffmpeg y nada más.
#
# Debian y no Alpine a propósito: el ffmpeg de Alpine no trae libx264
# (licencia GPL), y acá lo necesitamos para pasar de MJPEG a H.264.
# Verificado: ffmpeg 5.1 de bookworm tiene v4l2 + libx264.
FROM debian:bookworm-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core \
 && rm -rf /var/lib/apt/lists/*

COPY capture.sh /usr/local/bin/capture.sh
RUN chmod +x /usr/local/bin/capture.sh

# Sin HEALTHCHECK: el proceso vive mientras ffmpeg viva. Que ffmpeg
# reintente es trabajo del script, no de Docker.
ENTRYPOINT ["/usr/local/bin/capture.sh"]
