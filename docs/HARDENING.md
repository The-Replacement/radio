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

Stop affected services to avoid races, then align host ownership/permissions:

```bash
# Example: align data dirs to the operator/service account chosen in .env
sudo chown -R "$INDIEGEN_UID:$INDIEGEN_GID" data/indiegen
sudo chown -R "$MARIO_UID:$MARIO_GID" data/host
sudo chown -R "$CAM_UID:$CAM_GID" data/cam
sudo chown -R "$CADDY_UID:$CADDY_GID" data/caddy

# Align rw media/meta roots as needed by service roles
# sudo chown -R "$RADIO_UID:$RADIO_GID" music-audio jingles
# sudo chown -R "$INDIEGEN_UID:$INDIEGEN_GID" music music-audio data/indiegen

# Preserve secret restrictions (bearer-equivalent material)
chmod 600 secrets/tokens.txt caddy/tokens.caddy 2>/dev/null || true
```

Adjust ownership to the actual service split you deploy; the key invariant is:
each container can read/write only the host paths it needs, without creating
root-owned files.

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

## Documented exceptions / constraints

- **Camera device access:** `cam` requires `/dev/video0` (or configured device)
  and host group/GID mapping (`VIDEO_GID`) when non-root.
- **Caddy runtime state:** requires writable persistent storage (`./data/caddy`)
  and a deliberate internal listen/port-capability strategy for non-root.
- **Liquidsoap image behavior:** non-root compatibility and removal of
  `allow_root` must be validated against the pinned upstream image.
- **Secrets:** do not relax `0600` handling for `secrets/tokens.txt` and
  generated `caddy/tokens.caddy` (see #87).
