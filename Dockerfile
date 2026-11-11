# Mario — the host voice for the radio.
#
# Host-side install is the default (Approach A). This image is optional, for
# operators who prefer a container. It bundles Piper + the Castilian model;
# swap the model via MARIO_MODEL / volume for an Argentine (Pergolini) voice.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md LICENSE /app/
COPY src/ /app/src/
COPY presets/ /app/presets/

# Install with kokoro backend support (piper still default).
RUN pip install --no-cache-dir "/app[kokoro]"

VOLUME ["/music", "/data/host"]

ENTRYPOINT ["mario"]
CMD ["generate", "--music-dir", "/music", "--host-dir", "/data/host"]