# Runtime hardening (#83)

This document tracks implementation guidance for issue #83: reduce container
privileges/runtime users and prevent root-owned host artifacts.

## Current baseline

- `icecast` image runs as non-root user `icecast` (see `icecast/Dockerfile`).
- `indiegen` runs as `${INDIEGEN_UID}:${INDIEGEN_GID}` and is the reference for
  UID/GID alignment on bind-mounted write paths.
- `liquidsoap`, `cam`, `mario`, and `caddy` still require explicit non-root
  rollout work (compose user/caps/filesystem constraints and, where needed,
  Dockerfile/config changes).
- `data/caddy`, `data/cam`, `data/host`, `data/indiegen` may be root-owned on
  existing deployments; migrate ownership before enforcing non-root writers.

## Progress applied

**PR-A (safe baseline, no identity change intended):**
- `icecast`: `no-new-privileges:true`, `cap_drop: [ALL]`
- `mediamtx`: `no-new-privileges:true`, `cap_drop: [ALL]`
- `indiegen`: `no-new-privileges:true`, `cap_drop: [ALL]` (keeps existing
  mandatory `user`)

**PR-B (writer services, runtime identity/caps hardened; rebuild required):**
- `cam/Dockerfile`: creates non-root image user, ensures `/out` and
  `capture.sh` are usable; runtime still overridden by compose.
- `cam` compose: `user: ${CAM_UID:-1000}:${CAM_GID:-1000}`,
  `group_add: [${VIDEO_GID:-44}]` for `/dev/video0` access,
  `no-new-privileges:true`, `cap_drop: [ALL]`. Requires `./data/cam` writable by
  `CAM_UID/GID` and correct host `VIDEO_GID` for the camera device.
- `Dockerfile.mario`: creates non-root image user, makes `/app` readable, sets
  `HOME`; runtime still overridden by compose.
- `mario` compose: `user: ${MARIO_UID:-1000}:${MARIO_GID:-1000}`,
  `no-new-privileges:true`, `cap_drop: [ALL]`. Requires `./data/host` writable
  by `MARIO_UID/GID` (including `rss-cache`).

**PR-C (core path, runtime identity/caps hardened; validation required):**
- `liquidsoap/radio.liq`: `settings.init.allow_root` disabled for non-root
  runtime (re-enable only with a verified, documented exception).
- `liquidsoap` compose: `user: ${RADIO_UID:-1000}:${RADIO_GID:-1000}`,
  `no-new-privileges:true`, `cap_drop: [ALL]`. Requires read access for
  `RADIO_UID/GID` to mounted `./music-audio`, `./data/host`, `./jingles`, and
  `./liquidsoap/radio.liq`; validate pinned image compatibility.
- `caddy` compose: `user: ${CADDY_UID:-1000}:${CADDY_GID:-1000}`,
  `no-new-privileges:true`, `cap_drop: [ALL]`, `cap_add: [NET_BIND_SERVICE]`
  (keeps internal `:80/:443` listeners). Requires `./data/caddy` writable by
  `CADDY_UID/GID` and read access to `./caddy` (including `tokens.caddy`,
  bearer-equivalent secret material per #87), `./web`, `./data/cam`,
  `./music`, and `./data/host`. If `tokens.caddy`/config perms block reads,
  align owner/group/mode without relaxing secret handling unnecessarily.

`.env.example` documents ownership variables for rollout: `RADIO_UID/GID`,
`MARIO_UID/GID`, `CAM_UID/GID`, `CADDY_UID/GID`, and optional `VIDEO_GID`. After
these changes, rebuild/run affected services: `docker compose build cam mario`.

## Ownership migration (required before non-root writer rollout)

Stop affected services to avoid races if possible, then align host ownership/permissions.
Use the repo helper for known data paths:

```bash
# One-time/manual privileged fix (required now if root-owned artifacts exist)
sudo ./scripts/fix-data-ownership.sh

# Optional explicit chowns (same effect, adjustable per service split)
# sudo chown -R "$INDIEGEN_UID:$INDIEGEN_GID" data/indiegen
# sudo chown -R "$MARIO_UID:$MARIO_GID" data/host
# sudo chown -R "$CAM_UID:$CAM_GID" data/cam
# sudo chown -R "$CADDY_UID:$CADDY_GID" data/caddy

# Preserve secret restrictions (bearer-equivalent material)
chmod 600 secrets/tokens.txt caddy/tokens.caddy 2>/dev/null || true
```

Adjust ownership to the actual service split you deploy; the key invariant is:
each container can read/write only the host paths it needs, without creating
root-owned files.

## Frequent cleanup wiring (recommended)

Root-owned artifacts can recur until every writer path (containers, cron/hooks,
manual runs) is consistently aligned. To make cleanup automatable:

1. **Script:** `scripts/fix-data-ownership.sh` (idempotent, repo-local). It
   chowns only `data/{indiegen,host,cam,caddy}` using UID/GID vars from `.env`,
   first directly, then (if needed and available) via a disposable root Docker
   container (`alpine`, override with `FIX_OWNER_IMAGE`). Docker-group access is
   effectively privileged over mounted host paths — treat as such.
2. **Hooks:** `scripts/mario-refresh.sh` and `scripts/restart-on-signal.sh`
   attempt `sudo -n ./scripts/fix-data-ownership.sh` after runs (best-effort);
   `scripts/post-receive` attempts it on deploy. If passwordless sudo is not
   wired, the script can still self-heal via Docker when the user can run Docker.
3. **Scoped passwordless sudo (optional, one-time privileged install):** useful
   when Docker-based repair is unavailable/unwanted. Allow the deploy user to
   run only a fixed script path. Prefer a root-owned immutable copy to avoid
   escalation via editable repo files. Example:

   ```
   kbdejavu ALL=(root) NOPASSWD: /usr/local/sbin/radio-fix-data-ownership
   ```

   (`sudo install -o root -g root -m 0755 scripts/fix-data-ownership.sh /usr/local/sbin/radio-fix-data-ownership`)
4. **Verification:** `find data -not -user 1000 -o -not -group 1000` should be
   empty after cleanup (or limited to intentionally different owners).

## Rollout plan (next)

1. **Deploy-time migration & validation (required to realize #83):**
   - Set/confirm `RADIO_UID/GID`, `MARIO_UID/GID`, `CAM_UID/GID`, `CADDY_UID/GID`
     (and `VIDEO_GID`) in `.env`; align host ownership of `data/*` and any rw
     media/meta paths to those identities.
   - Rebuild changed images: `docker compose build cam mario` (and others if
     Dockerfiles change later).
   - Validate runtime: `liquidsoap` connects/plays, `caddy` serves gated
     endpoints + cert/storage writes in `data/caddy`, `cam` writes segments,
     `mario` generates without root-owned outputs, `icecast`/`mediamtx` healthy.
2. **Filesystem hardening:** enable `read_only: true` + targeted `tmpfs` mounts
   per service after runtime validation (avoid speculative breakage).
3. **Issue closure (#83):** confirm non-root/runtime caps in effect, no new
   root-owned bind-mount artifacts, and documented exceptions accepted (camera
   device/group, Caddy writable storage + `NET_BIND_SERVICE`, pinned
   liquidsoap/caddy image behavior, secrets handling constraints from #87).

## Extra liquidsoap instances (#102)

Starting liquidsoap is expensive in anonymous memory: **~330 MiB before it
settles at ~80 MB** (measured 2026-11-18: `liquidsoap --check` peaks at 337 MiB,
a full run at 328 MiB, both inside the pinned image). A streaming instance then
stays flat — 35 minutes, 6 and 18 tracks, with and without the `cross`
transition, all settled at 77–82 MB.

That startup cost is the whole story of the 2026-11-16 OOM lines. Six one-off
`docker compose run liquidsoap` containers (0.6 s each, ~420 MB peak) and one
`docker exec ... liquidsoap` inside the service were OOM-killed at
`anon-rss ~522 MB`. The service itself never died: `RestartCount` stayed 0 and
its PID appears in every kernel task dump while the extras were being killed.

Rules that follow:

- **Never run a second liquidsoap inside `radio-liquidsoap`.** An exec'd
  process shares the encoder's cgroup and its 512 MiB cap; the kernel picks the
  larger task, and one day that will be the encoder.
- **Probes and checks run in a separate container**, capped, with
  `--network none` and an output to a file — never against the live mount.
- `scripts/spacer-race.sh` holds `/tmp/spacer-race.lock` and refuses to start if
  a foreign liquidsoap process is already inside the container.
- `radio-job.sh verify` records `liquidsoap_extra` (processes in the service
  beyond PID 1) and `liquidsoap_oneoffs` (`radio-liquidsoap-run-*` containers)
  in `data/status.json`, fails the `liq` line, and closes the gate while either
  is non-zero.

## cgroup priority (#105)

Batch work (indiegen, mario) runs in the `radio-batch.slice` systemd slice with
`CPUWeight=20`; the audio path stays in the default slice. The compose services
set `cgroup_parent: radio-batch.slice` for `indiegen` and `mario` only. The slice
unit lives in the separate `infra` repo (`infra/deploy/systemd/radio-batch.slice`) and is installed by
`infra/deploy/bootstrap.sh`. Requires Docker on the `systemd` cgroup driver; on
a host still using `cgroupfs` the slice is invisible to containers and the
`cgroup_parent` reference fails at start — verify with `docker info | grep -i cgroup`.

## Sensitive operational data (#97)

- `data/status.json` is served under the door (`/host/status.json`). It carries
  host, deploy commit, track title, and resource metrics. These are operational,
  not secret, but treat the file as gated: never publish it outside `@gate`.
- `data/*.log` (`job.log`, `gc.log`, `deploy.log`, `restart-on-signal.log`,
  `mario-*.log`) persist host paths, changed-file lists, and docker output.
  They are gitignored and stay on the box; do not copy them off-box unredacted.
- `token.sh ls` masks tokens (last 4 chars only). `token.sh reveal` prints full
  invitation/camera URLs by design — run it only in a trusted terminal and
  never capture its stdout in logs or screenshots.
- Smoke tests pass tokens via curl argv (`scripts/smoke.d/20-auth-and-media.sh`).
  They are integration gates for the box only; do not run them on shared hosts
  where process listings are visible to other users.
- Container env holds Icecast passwords (`docker inspect`); Caddy logs at ERROR
  to avoid token-in-URL logging; Liquidsoap only logs an empty password.

## Documented exceptions / constraints

- **Camera device access:** `cam` requires `/dev/video0` (or configured device)
  and host group/GID mapping (`VIDEO_GID`) when non-root.
- **Caddy runtime state:** requires writable persistent storage (`./data/caddy`)
  and a deliberate internal listen/port-capability strategy for non-root.
- **Liquidsoap image behavior:** non-root compatibility and removal of
  `allow_root` must be validated against the pinned upstream image.
- **Secrets:** do not relax `0600` handling for `secrets/tokens.txt` and
  generated `caddy/tokens.caddy` (see #87).
- **Mario runtime identity (#99):** image bakes uid 10001 but compose overrides
  to `MARIO_UID/GID` (default 1000, like the indiegen default). `MARIO_UID` should equal
  `INDIEGEN_UID` (or `./music` and the model dir must be group/other-readable)
  so Mario can read catalog/sidecars and the Kokoro model. `HOME`/`XDG_CACHE_HOME`
  are set to `/tmp` because the image's `/home/mario` is owned by uid 10001.
  `data/host` (incl. `rss-cache`) is the only writable bind mount.
