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
