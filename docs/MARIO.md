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

## Cadence and scheduling

Time is a quantized bucket (`MARIO_TIME_BUCKET_HOURS`, 6 in this deployment to
match the indiegen rotation of 21600 s). Transitions and liners are keyed by it:
a bucket flip renders every segment for the new bucket once, and any rerun
inside the same bucket is a cache hit that exits in about a second.

Weather and news do **not** ride that bucket. Each has a bucket-less playout name
(`weather.wav`, `rss.wav`) that `scripts/mario-slot.sh` republishes atomically, so
their cadence is independent of the bucket width. Liquidsoap prefers those names
and falls back to the bucket-scoped ones, so an older host dir still airs.

| Family | Refreshed | Mechanism |
|---|---|---|
| transitions, liners | per 6 h bucket, plus every library rotation | `scripts/mario-refresh.sh` (hourly cron: cache hit unless the bucket flipped) and the rotation handler |
| weather | every 10 min | `scripts/mario-slot.sh weather` — fetch Open-Meteo, then render, in one container |
| news | every 30 min | `scripts/mario-slot.sh rss`, with a 30-minute bucket so the feed rotation moves every half hour |

### What invalidates a cached segment

Filenames are stable, so the cache decides on a sidecar
(`<name>.wav.meta.json`) holding a sha256 of `(template_version, voice, speed,
lang, text)`. Consequences worth knowing before you touch anything:

- **Editing any spoken text re-renders it**, whichever family it belongs to. The
  text is always in the identity.
- **The version knob is per family**, not global:

  | Family | Knob |
  |---|---|
  | transitions | `MARIO_TEMPLATE_VERSION` (config) |
  | liners | `template_version` in `presets/liners.yaml` |
  | weather | `template_version` in `presets/weather.yaml` |
  | news | `template_version` in `presets/rss.yaml` |

  Bumping `MARIO_TEMPLATE_VERSION` does **not** invalidate weather or news: they
  carry their preset's version. Use the matching one.
- **Changing voice or speed re-renders everything**, including cached segments.
  That is new: until 2026-11-14 those were not part of the key, so switching
  voices left the old voice on air for any segment already rendered.
- A segment with no sidecar (rendered before this existed) is re-rendered once
  and cached again afterwards.

Cron on the box:

```
0 * * * *    /home/kbdejavu/servers/radio/scripts/radio-job.sh tts
3,13,23,33,43,53 * * * * /home/kbdejavu/servers/radio/scripts/mario-slot.sh weather
7,37 * * * * /home/kbdejavu/servers/radio/scripts/mario-slot.sh rss
* * * * *    /home/kbdejavu/servers/radio/scripts/restart-on-signal.sh
```

Both are offset off the hour on purpose: the hourly job starts at :00 and a
bucket flip keeps it rendering for ~6.5 min, so a slot run at :00 would just
lose the render lock. The offsets also keep the two slots apart from each other.
A skipped tick is not an emergency: the freshness limits (1500 s weather,
3900 s news) tolerate a couple of misses before `radio-job.sh status` complains.

Every path shares three guards, so they cannot pile up (that is not theoretical —
on 2026-11-14 a missing one produced 30 concurrent renders and load 98):

- **one render at a time** — `/tmp/mario-refresh.lock`; a second tick skips and logs it.
- **the box gate** — `radio-job.sh preflight` (temperature, memory pressure, load,
  concurrent renders). A deferred run is a correct outcome, not a failure.
- **a progress watchdog** — `scripts/lib/render-watchdog.sh` kills a render that
  stops producing segments. Deliberately not a fixed timeout: duration depends on
  the `cpus` cap and on the library size (transitions are `n*(n-1)`), and a
  cap-coupled timeout once killed every rotation pre-render, silently.

Slot runs pass `--no-marker` (playout builds every transition path from
`current-bucket`, and the news slot uses a narrower bucket, so it must not write
it) and `--keep-buckets 1` (retention must not depend on the hourly job).

Deploy note: `scripts/post-receive` (installed as the receive hook) rebuilds the
`radio-mario` image automatically when a push touches `src/`, `presets/`,
`pyproject.toml`, `uv.lock`, `Dockerfile.mario` or `docker-compose.yml`, and
fails loudly if the build breaks. A push that only touches `web/` or docs does
not pay the build. The new image is picked up by the next scheduled run; the
stream is never restarted by a push — `liquidsoap/radio.liq` changes need a
deliberate `docker compose restart liquidsoap` (~40 s of dead air).

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
- `note: ownership repair skipped (no passwordless sudo for this script)` in
  `data/mario-refresh.log` / `data/restart-on-signal.log`: the #83 repair needs a
  scoped sudoers entry. Either add it, or expect `data/host` to keep whatever
  ownership the containers wrote:

  ```
  # /etc/sudoers.d/radio-ownership
  kbdejavu ALL=(root) NOPASSWD: /home/kbdejavu/servers/radio/scripts/fix-data-ownership.sh
  ```

- The meteorologist or the newsreader went quiet: `scripts/radio-job.sh status`
  prints `weather refreshed Ns ago` and `news refreshed Ns ago` (limits 1500 s and
  3900 s). An age of `-1` means the alias is missing or dangling — a publish
  problem, not a staleness problem. `data/mario-slot.log` has one line per run.

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

## Weather slot

Off by default. Point `--weather-file` (or `MARIO_WEATHER_FILE`) at a JSON
snapshot to enable one spoken weather segment per time bucket:

```json
{
  "temperature_c": 18.5,
  "wind_dir_deg": 225.0,
  "wind_speed_kmh": 12.0,
  "sky": "partly_cloudy",
  "observed_at": "2026-11-13T14:00:00Z"
}
```

Valid `sky` values: `clear`, `partly_cloudy`, `overcast`, `rain`, `storm`,
`snow`, `fog`. `observed_at` is optional and does not affect the spoken text
or cache key (only the spoken fields are hashed).

Behaviour worth knowing:

- **One segment per bucket.** The text is rendered once per time bucket and
  cached by `(weather_snapshot_hash, bucket)`. Re-running with the same
  snapshot produces no new renders.
- **Fallback on missing/invalid snapshot.** If the file is missing or fails
  validation, a deterministic fallback line is spoken instead.
- **Playout reads one name:** `weather_<bucket>.wav` (symlink to the
  hash-derived cache file `weather_{hash[:8]}_<bucket>.wav`).
- **A sample snapshot** is available at `presets/weather-snapshot.example.json`.

## Voice / backends

- **Kokoro (default):** 82M TTS via `kokoro-onnx` using local model + voices files. Default voice is Spanish female `ef_dora` (`--voice`, `--model`, `--voices-path`), local/offline.
- **Limitation (2a, Kokoro-only / no Piper):** `ef_dora` is a Spanish (espeak-ng `es`, Spain-accented) voice, not Argentine (rioplatense). A true rioplatense accent/tone is not achievable with available Kokoro v1.0 voices; host copy remains rioplatense Spanish in `presets/`.
- Swapping to another bundled Kokoro voice is a matter of pointing `--voice`/`--model`/`--voices-path` at different local assets. Voice cloning or a true Argentine/rioplatense timbre requires a different local/offline backend (future work).

## License

Apache-2.0.
