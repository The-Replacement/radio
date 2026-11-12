# Mario — the host voice (integration notes)

Mario is the host voice for the radio: a deterministic TTS generator (package
`mario`, source under `src/mario/`, presets under `presets/`, image
`Dockerfile.mario`). It reads this radio's music library and renders short
spoken Host segments, cached by (current_seed, next_seed, time_bucket), into
`./data/host/`. Liquidsoap plays them between tracks. It never touches the
stream and never restarts anything.

## Deploy on the box (mirroring indiegenerator)

Mario runs as a container, built from this repo (context `./`, dockerfile
`Dockerfile.mario`):

```bash
cd /home/kbdejavu/servers/radio
docker compose build mario
```

The image is rebuilt automatically by `scripts/post-receive` when a push touches
`src/`, `presets/`, `pyproject.toml`, `uv.lock`, `Dockerfile.mario` or
`docker-compose.yml`. The model must be present on the box at the path mounted
in docker-compose.yml (`/home/kbdejavu/Models/...`). See the `mario` service
definition.

## Wiring (already in the repo)

- `docker-compose.yml`: `./data/host` is mounted into Liquidsoap at `/host:ro`
  and into Caddy at `/srv-host:ro`.
- `caddy/Caddyfile`: `/host/*` is served under the existing `@gate` (cookie/token).
- `liquidsoap/radio.liq`: a `cross` transition (duration 3.0 s) builds a spacer list per track boundary: the cached host segment when present; a station ident scanned from `/host` (`liner_*_{seed}_{bucket}.wav`, sorted, first match) included on every transition when no host segment is available and only when the incoming seed is even when a host segment is present; and an occasional jingle from `/jingles` (seed mod pool size) played when the seed mod 5 == 0 and fewer than 2 spacers are collected. At most 2 spacers total; otherwise a 2.0 s crossfade. Outer `mksafe` covers misses.
- `docker-compose.yml`: `./jingles` is mounted into Liquidsoap at `/jingles:ro` (read-only). Jingles are never placed under `./music`, so the playlist does not scan them.
- `scripts/restart-on-signal.sh`: runs `mario generate` before restarting
  Liquidsoap on a library rotation.
- `scripts/smoke.sh`: Host group (SKIP when no cached segment).

## Hourly bucket refresh

Time is a quantized bucket (default 1 hour). To keep the "time" announcement
fresh without rotating music, run Mario hourly via cron:

```
0 * * * * /home/kbdejavu/servers/radio/scripts/mario-refresh.sh
```

Deploy note: `scripts/post-receive` (installed as the receive hook) rebuilds the
`radio-mario` image automatically when a push touches `src/`, `presets/`,
`pyproject.toml`, `uv.lock`, `Dockerfile.mario` or `docker-compose.yml`, and
fails loudly if the build breaks. A push that only touches `web/` or docs does
not pay the build. The new image is picked up by the next hourly run of
`mario-refresh.sh`; the stream is never restarted by a push.

where `scripts/mario-refresh.sh` is:

```bash
#!/usr/bin/env bash
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MARIO_MUSIC_DIR="$ROOT/music" MARIO_HOST_DIR="$ROOT/data/host" \
  mario generate --music-dir "$ROOT/music" --host-dir "$ROOT/data/host" \
  >/dev/null 2>&1 || true
```

## Voice (Pergolini tribute)

The aspiration is an Argentine (rioplatense) voice. The default model is the
Castilian Piper voice shipped with the box. Swap it by pointing `MARIO_MODEL`
(or `--model`) at an Argentine Piper model. Voice cloning is future work.

## Troubleshooting

- No segments on air: check `ls data/host/*.wav`; run `mario generate` by hand;
  check `docker compose logs liquidsoap` for `/host` read errors.
- Wrong/missing seed: the COMMENT tag (`seed=...`) must be present; fallback is
  the `_s{seed}` filename suffix. Both come from indiegenerator.

---

# Paquete `mario` (absorbido desde el repo mario en la fusión)

# Mario

Mario is the host voice for the radio — a deterministic TTS generator in living
tribute to Mario Pergolini.

The radio is a station with no host. Mario is the host: a voice that speaks
between tracks, announcing what played, what plays next, the time, and a
one-line thought. Deterministic from seed + track metadata: the same inputs
produce the same words, byte for byte. Re-listening to a transition re-runs
the same voice.

Mario is a separate generator, like indiegenerator. It reads a radio music
library (catalog.json + sidecars) and renders short spoken segments with Piper,
cached by `(current_seed, next_seed, time_bucket)`. It never touches the
stream and never restarts anything.

## Install

```bash
pip install /path/to/mario
```

Dependencies are pinned in `uv.lock`. The container image installs from it with
`uv sync --frozen` (transitive pins included — the TTS numerics, and therefore
the rendered bytes, depend on them). For a host install, pip uses the exact
direct pins in `pyproject.toml`; use `uv sync --frozen` if you want the full
transitive set.

## Run

```bash
# Piper (default)
mario generate --music-dir /music --host-dir /data/host --model /models/piper/es_ES-sharvard-medium.onnx

# Kokoro (requires: pip install 'mario[kokoro]' + local model & voices files)
mario generate --backend kokoro --music-dir /music --host-dir /data/host \
  --model /models/kokoro/kokoro-v1.0.onnx \
  --voices-path /models/kokoro/voices-v1.0.bin \
  --voice af_sarah --model-lang en-us
```

## Design notes

- Determinism: Piper uses `--noise-scale 0 --noise-w-scale 0`. Kokoro uses fixed inference parameters; verify hash stability for your setup if byte-identical caching is required.
- Time is a quantized bucket (default 1 hour), included in the cache key.
- All ordered pairs of the current library are pre-rendered (N*(N-1) segments).
- Segments live under `./data/host/`, never under `./music/` (so Liquidsoap
  does not scan them into the playlist).
- Network I/O happens only in the RSS fetch module (`mario/feedfetch.py`), at
  generation time on a cache miss. Transitions, liners, and weather are local.
  On-air playback uses cached WAVs, never the network.

## RSS news slot

Off by default. Point `--rss-feeds-file` (or `MARIO_RSS_FEEDS_FILE`) at a feeds
file to enable one spoken news item per time bucket:

```yaml
feeds:
  - name: Cultura
    url: https://example.org/rss/cultura/
  - https://example.org/rss/deportes/      # bare URL also works
```

`name` is optional and only feeds the filesystem slug (ASCII: an accent becomes
a hyphen). Any config mistake raises loudly instead of being ignored.

Behaviour worth knowing:

- **Rotation, not truncation.** The starting feed for a bucket is
  `sha256(bucket) % len(feeds)`, so every feed gets its turn over the day.
  `--rss-max-items-per-cycle` (default 1) caps how many segments *air*; the
  walk stops as soon as it is met.
- **Empty feeds are skipped, not announced.** A category with no items is
  passed over silently; a single fallback line is spoken only when *every*
  candidate fails that hour.
- **Fetch failures stop early.** Two consecutive failures end the walk, so a
  dead network costs two timeouts, not one per feed.
- **Playout reads one name:** `rss_<bucket>.wav` (symlink to the hash-derived
  file), mirroring `weather_<bucket>.wav`. The per-feed alias
  `rss_<slug>_<bucket>.wav` exists for debugging only.
- **Set `MARIO_RSS_CACHE_DIR` in containers.** The default derives from
  `host_dir.parent`, which is not a mount in the shipped image — the cache
  would die with the container and every run would refetch from cold.

## Voice / backends

- **Piper (default):** lightweight, deterministic, local `.onnx` voices.
- **Kokoro:** higher-quality 82M TTS via `kokoro-onnx` (local model + voices). Better prosody; install extra `mario[kokoro]` and supply `--model`, `--voices-path`, `--voice`.

The aspiration is a voice inspired by Mario Pergolini: Argentine (rioplatense),
energetic, ironic, distinctive. Swapping to another voice is a matter of
pointing `--model`/`--voice` at different local assets. Voice cloning or a
true Argentine/Stern-like timbre requires a cloning-capable backend (future work).

## License

Apache-2.0.
