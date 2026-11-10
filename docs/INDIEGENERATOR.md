# indiegenerator ↔ radio

Integration of the music generator (`indiegenerator`) with the radio stack.

## What it does

The `indiegen` service (see `docker-compose.yml`) runs on the mainframe next to
the radio and, on every cycle:

1. Renders a fresh batch of tracks (preset `station`, advancing seeds).
2. **Replaces the whole library** in `./music` (deletes old tracks, publishes
   the new ones atomically).
3. Restarts `radio-liquidsoap` so the in-memory playlist forgets the deletions.
   Liquidsoap does `reload_mode="watch"` for additions, but keeps deletions in
   memory (`radio.liq`): without a restart, the deleted tracks keep playing like
   ghosts.

## Trigger

- **Continuous**: `INDIEGEN_INTERVAL_SECONDS` (default 6 h). On start it does NOT
  run a cycle (`--no-run-on-start` in the compose): the first cycle arrives by
  interval or by trigger. On purpose — a cold cycle would replace all of
  `./music` during `up -d` (and there is an untracked batch on the box).
- **Push to indiegenerator**: the `scripts/post-receive` hook of the
  `indiegenerator` repo (installed on the mainframe's remote) rebuilds the
  service and, if the policy is on, touches `data/indiegen/trigger` and fires an
  immediate cycle. The policy is read from the radio's `.env` on every push — it
  is **not** hardcoded in the hook:

  - `INDIEGEN_AUTO_TRIGGER=1` → every push rotates the library (full render
    ~7 min + liquidsoap restart + ~10 s of cut).
  - `INDIEGEN_AUTO_TRIGGER=0` (or absent) → the push only deploys code; the
    content rotates by interval or with `scripts/indiegen-cycle.sh`.

  In this deployment it is `1` by the operator's decision. The cost is real: a
  push is a code change, rotating over a comment is expensive. If the hook
  cannot write the trigger (permissions on `data/indiegen`), it records it as
  `FAIL` in `data/indiegen/deploy.log` and exits with an error — it does not
  fail silently.

## Requirements on the mainframe

- `~/servers/radio` (this repo) and `~/servers/indiegenerator` (build context,
  `INDIEGEN_CONTEXT`) as sibling repos, or adjust `INDIEGEN_CONTEXT`.
- Restart in **signal** mode (default): the generator writes
  `data/indiegen/restart-request` on rotation, and a host watcher runs the
  `docker compose restart liquidsoap`. To install it:

```bash
# watcher: runs every minute, as kbdejavu (docker group). No sudo.
crontab -l | { cat; echo "* * * * * /home/kbdejavu/servers/radio/scripts/restart-on-signal.sh"; } | crontab -
```

`/var/run/docker.sock` is not mounted in any container: the socket in the
generator gave root-equivalent docker control over the whole host (which also
runs jellyfin/searxng/archive). The liquidsoap restart is the radio's decision,
executed by the host via cron.

## Operation

```bash
# immediate cycle (from the mainframe host)
./scripts/indiegen-cycle.sh

# logs
docker compose logs -f indiegen

# disable automatic generation without touching the radio
docker compose stop indiegen
```

### `.tmp` and liquidsoap's tmpfs

The generator publishes with an atomic rename: it writes each file into
`./music/.tmp` and renames it to the final name. That staging lives inside the
tree liquidsoap scans, and `playlist("/music")` walks subdirectories — so
liquidsoap tried to decode half-written files (and the old `.mix` files left
there), in a loop, one error per attempt.

The `liquidsoap` service mounts an empty `tmpfs` over `/music/.tmp`: the
generator still sees its real folder (the publish does not change), liquidsoap
sees an empty directory. **Do not remove it** thinking it is a leftover. Tested
with a canary on the host: 0 entries inside the container. (Before choosing
this, these were discarded: `playlist` has no `recursive`; `request.directory`
does not exist in 2.4.6; and `check_next` is useless because `request.filename`
returns `""` until the request is resolved.)

### uid/gid: the generator runs as your user, not as root

The `indiegen` service uses `user: "${INDIEGEN_UID}:${INDIEGEN_GID}"`, taken
from `.env`. It is mandatory (the compose does not start without those values):
get them with `id -u` / `id -g` on the box (here `1000:1000`).

Why: by default a container runs as root and everything it writes on the volumes
ends up `root:root`. Reading and deleting still work, but any host tool that
wants to **rewrite** those files hits `EACCES`. Measured: `echo >> state.json`
as uid 1000 against a `root:root` `state.json` → `Permission denied`; `mkdir`
inside a `root:root` `runs/` → `Permission denied`. Since both are touched on
every cycle, the wrong uid does not break the deploy: it breaks the next cycle.

**If you change the uid on a deployment that already ran**, the artefacts root
left behind have to be migrated. Owning the directory is enough to delete them,
but not to rewrite them:

```bash
cd ~/servers/radio
cp data/indiegen/state.json data/backup/state-cycleN.json   # preserve the counter
rm -rf data/indiegen/runs data/indiegen/restart-request data/indiegen/state.json
cp data/backup/state-cycleN.json data/indiegen/state.json   # now it is yours
```

The old `music/*.flac` files owned `root:root` do **not** need chown: the
publish renames over the final name, and `rename(2)` only asks for write
permission on the directory, not on the destination file.

**Do NOT run plain `docker compose up -d --build indiegen`.** `--build` also
rebuilds the icecast image (its service has `build: ./icecast`) and recreates
the container → the stream cuts for ~40 s (measured: EPIPE + liquidsoap
reconnecting at 4 s, the whole listener mount disconnected). To rebuild ONLY the
generator:

```bash
docker compose build indiegen
docker compose up -d --no-deps indiegen
```

`--no-deps` is the part that matters: it names ONLY `indiegen`, so compose does
not touch the `indiegen → liquidsoap → icecast` chain. **Do not add
`--no-recreate`**: its contract is "if the container already exists, do not
recreate it", which with a new image would leave the old one running silently
(`docker compose up --help`, v2.40.3). Recreating indiegen is free —it serves no
audio—; recreating icecast costs ~40 s of stream.

### Two measured timings worth keeping in mind

1. **The watcher is cron, it is not instant.** Between the generator writing
   `restart-request` and cron seeing it, up to ~60 s pass (measured: 10 s and
   53 s in two consecutive cycles, depending on where the tick falls). In that
   window liquidsoap still has the old playlist whose files no longer exist; if
   it tries one, the decode fails and it skips. Measured so far: zero decode
   errors and zero icecast disconnections, but the exposure is real. If it ever
   bothers, it is fixed on the radio side (a finer poll than `* * * * *`), not
   in the generator.
2. **A push during a render kills that render.** The hook does `up -d` (it
   recreates the container) and only afterwards writes the trigger, so the cycle
   in flight dies and a new one starts from scratch. The library stays
   consistent (the publish comes after the complete batch), but the CPU is paid
   twice. With ~7 min renders and burst pushes, the collision is realistic: if
   you are going to push several commits, batch them.

### A cycle can install FEWER than `INDIEGEN_COUNT` tracks

A track that does not pass the gates is skipped (logged as `WARNING skipping
track`) and the cycle goes on: 19 files installed instead of 21 in cycle 4
because seed 1036 failed gate 13. Nine tracks on air are better than zero, but
anything that assumes "one cycle = `COUNT` tracks" is wrong: the real count is
`<= COUNT`.

Careful with the corollary: a gate's verdict depends on `(seed, preset, form,
batch context)`, not on the seed alone. `batch()` distributes forms by slot
(`batch_forms(..., count)[index]`), so the same seed rendered alone can pass
gate 13 and fail it at position 6 of a batch of 10 — different form, different
duration/structure, different bar count. It is not a bug in serve; it is the
consequence of the gate looking at the plan.

## Coordination

Changes that affect the music↔radio contract (format/name, the `./music` mount,
the liquidsoap restart, the `INDIEGEN_*` variables) must be reviewed in both
repos: `juanmanueldaza/indiegenerator` and `juanmanueldaza/radio`.
