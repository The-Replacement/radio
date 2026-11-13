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

## Voice

Mario's on-air identity keeps the host name **Mario**. The default synthesized
delivery is a **Spanish female** voice via **Kokoro** (`ef_dora`, local/offline).

**Limitation (#100, re-scoped 2a, Kokoro-only / no Piper):** `ef_dora` is a
Spanish voice (Kokoro v1.0 / espeak-ng `es`, Spain-accented), **not** Argentine
(rioplatense). A true rioplatense accent and the originally requested tone
cannot be delivered with the available Kokoro voices; original #100 acceptance
criteria for Argentine accent/verification (AC1/AC3 as written) are not met
under this scope. Host copy in `presets/` remains rioplatense Spanish. Achieving
a true Argentine/Stern-like timbre or accent requires a different local/offline
backend/voice (future work).

### Default (reproducible, local/offline)

- **Backend:** Kokoro (`kokoro-onnx`, local inference; no Piper)
- **Voice:** `ef_dora` — Spanish female (`MARIO_VOICE=ef_dora`)
- **Model files (both required, local):**
  - `kokoro-v1.0.onnx` (`MARIO_MODEL=/models/kokoro/kokoro-v1.0.onnx`, default)
  - `voices-v1.0.bin` (`MARIO_VOICES_PATH=/models/kokoro/voices-v1.0.bin`, default)
- **Language:** `MARIO_MODEL_LANG=es` (mapped to Kokoro `es`)
- **Speed:** `MARIO_TTS_SPEED` / `--tts-speed` (default `1.0`, configurable)
- **Determinism:** fixed inference parameters/speed and outputs cached by
  segment key. Byte-identical audio depends on identical model + voices files,
  runtime/dependency versions (see `uv.lock`), and environment; pin and deploy
  the same artifacts for reproducible results.

### Provisioning the model

1. Download the exact upstream artifacts (v1.0 files used by this stack):
   - `kokoro-v1.0.onnx` and `voices-v1.0.bin` (see kokoro-onnx releases/assets,
     e.g. `model-files-v1.1` in https://github.com/thewh1teagle/kokoro-onnx/releases).
2. Place them on the host under the directory mounted to `/models/kokoro`
   (default `/home/kbdejavu/Models/kokoro` in `docker-compose.yml`).
3. Verify integrity and record for reproducibility (recommended):
   - `sha256sum kokoro-v1.0.onnx voices-v1.0.bin` and store the checksums plus
     the upstream source/revision/tag you fetched.
4. Confirm the container sees them at `MARIO_MODEL` / `MARIO_VOICES_PATH`.

### Validation

```bash
# plan outputs (no render, no model required beyond config resolution in dry-run paths)
mario generate --dry-run --music-dir ./music --host-dir ./data/host

# render/generate for real (requires model+voices mounted/available)
mario generate --music-dir ./music --host-dir ./data/host
ls data/host/*.wav
```

Review generated host segments in normal playout paths when validating changes.
Under 2a, expect Spanish female `ef_dora` delivery (Spain-accented), not
Argentine/rioplatense.

### Licensing and source

- **Model/runtime:** Kokoro-82M / `kokoro-onnx` upstream artifacts and code.
  - Model/voices: https://huggingface.co/hexgrad/Kokoro-82M (see `VOICES.md`)
  - Runtime/lib: https://github.com/thewh1teagle/kokoro-onnx
- **License/terms:** comply with the license/terms of the exact model, voices,
  and runtime versions you deploy (e.g. Kokoro-82M Apache-2.0; verify current
  upstream terms). Record the upstream revision/tag and file checksums alongside
  deployed artifacts for provenance and reproducibility.

### Swapping voices

- **Another Kokoro voice:** set `MARIO_VOICE` and/or `MARIO_MODEL`,
  `MARIO_VOICES_PATH`, `MARIO_MODEL_LANG` as needed (Kokoro v1.0 Spanish voices:
  female `ef_dora`, male `em_alex`, `em_santa`). No bundled Argentine/rioplatense
  voice is available in v1.0.
- Host name remains **Mario** regardless of voice/model choice. Voice cloning or
  a true Argentine/rioplatense voice requires a different local/offline backend
  (future work).

## Troubleshooting

- No segments on air: check `ls data/host/*.wav`; run `mario generate` by hand;
  check `docker compose logs liquidsoap` for `/host` read errors.
- Wrong/missing seed: the COMMENT tag (`seed=...`) must be present; fallback is
  the `_s{seed}` filename suffix. Both come from indiegenerator.

---

# `mario` package (absorbed from the mario repository in the fusion)

## Mario

Mario is the host voice for the radio — a deterministic TTS generator in living
tribute to Mario Pergolini.

The radio is a station with no host. Mario is the host: a voice that speaks
between tracks, announcing what played, what plays next, the time, and a
one-line thought. Deterministic from seed + track metadata: the same inputs
produce the same words, byte for byte. Re-listening to a transition re-runs
the same voice.

Mario is a separate generator, like indiegenerator. It reads a radio music
library (catalog.json + sidecars) and renders short spoken segments with Kokoro,
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
# Kokoro (default): Spanish female voice ef_dora
mario generate --music-dir /music --host-dir /data/host \
  --model /models/kokoro/kokoro-v1.0.onnx \
  --voices-path /models/kokoro/voices-v1.0.bin \
  --voice ef_dora --model-lang es
```

## Design notes

- Determinism: fixed Kokoro inference parameters/speed and cached outputs by segment key. Byte-identical caching depends on model/runtime/version stability; pin and deploy the same Kokoro model, voices, and dependency set (`uv.lock`) for reproducible results.
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

- **Kokoro (default):** 82M TTS via `kokoro-onnx` using local model + voices files. Default voice is Spanish female `ef_dora` (`--voice`, `--model`, `--voices-path`), local/offline.
- **Limitation (2a, Kokoro-only / no Piper):** `ef_dora` is a Spanish (espeak-ng `es`, Spain-accented) voice, not Argentine (rioplatense). A true rioplatense accent/tone is not achievable with available Kokoro v1.0 voices; host copy remains rioplatense Spanish in `presets/`.
- Swapping to another bundled Kokoro voice is a matter of pointing `--voice`/`--model`/`--voices-path` at different local assets. Voice cloning or a true Argentine/rioplatense timbre requires a different local/offline backend (future work).

## License

Apache-2.0.
