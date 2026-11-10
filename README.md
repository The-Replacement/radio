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

## Run

```bash
mario generate --music-dir /music --host-dir /data/host --model /models/piper/es_ES-sharvard-medium.onnx
```

## Design notes

- Determinism: Piper is invoked with `--noise-scale 0 --noise-w-scale 0`.
- Time is a quantized bucket (default 1 hour), included in the cache key.
- All ordered pairs of the current library are pre-rendered (N*(N-1) segments).
- Segments live under `./data/host/`, never under `./music/` (so Liquidsoap
  does not scan them into the playlist).
- No network calls at generation time. The Piper model is a local file.

## Voice

The aspiration is a voice inspired by Mario Pergolini: Argentine (rioplatense),
energetic, ironic, distinctive. The default model shipped is a Castilian
Spanish Piper voice; swapping to an Argentine voice model is a matter of
pointing `--model` (or `MARIO_MODEL`) at a different `.onnx`. Voice cloning
or an Argentine Piper model is future work.

## License

Apache-2.0.
