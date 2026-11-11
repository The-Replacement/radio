# Mario — the host voice (integration notes)

Mario is a separate generator (repo: https://github.com/juanmanueldaza/mario). It
reads this radio's music library and renders short spoken Host segments, cached
by (current_seed, next_seed, time_bucket), into `./data/host/`. Liquidsoap plays
them between tracks.

## Deploy on the box (mirroring indiegenerator)

Mario runs as a container, built from the `mario` repo (context `MARIO_CONTEXT`,
default `../mario`):

```bash
cd /home/kbdejavu/servers/radio
docker compose build mario
```

The model must be present on the box at the path mounted in docker-compose.yml
(`/home/kbdejavu/Models/piper/...`). See the `mario` service definition.

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