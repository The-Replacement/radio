# La cámara: ffmpeg y nada más.
#
# Debian y no Alpine a propósito: el ffmpeg de Alpine no trae libx264
# (licencia GPL), y acá lo necesitamos para pasar de MJPEG a H.264.
# Verificado: ffmpeg 5.1 de bookworm tiene v4l2 + libx264.
FROM debian:bookworm-slim@sha256:7c7b2c966bc9ee8cedfeef67e0e279108992c77681fa595db4a9d65c06ccc587

# OCI provenance: the image carries the source revision it was built from, so
# a running container can be traced to a commit instead of to a guess. The
# receive hook builds with VCS_REF=<the pushed SHA>; a manual build leaves it
# empty and the image says so.
ARG VCS_REF=""
ARG OCI_SOURCE="https://github.com/The-Replacement/radio"
LABEL org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.source="${OCI_SOURCE}"

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd -r -g 10001 cam \
 && useradd -r -u 10001 -g cam -d /nonexistent -s /usr/sbin/nologin cam

COPY capture.sh /usr/local/bin/capture.sh
RUN chmod 0755 /usr/local/bin/capture.sh \
 && mkdir -p /out \
 && chown cam:cam /out

# Runtime user is typically overridden by compose (${CAM_UID}:${CAM_GID}) with
# group_add for the host video device. Keep image runnable as non-root by default.
USER cam

# Sin HEALTHCHECK: el proceso vive mientras ffmpeg viva. Que ffmpeg
# reintente es trabajo del script, no de Docker.
ENTRYPOINT ["/usr/local/bin/capture.sh"]
