# Run your own door

This is the **door-only** path for the radio: token-gated streaming that answers
**404** (never 401) without a valid credential, with cookie exchange, per-person
revocation, query stripping, and no published upstream ports. It is the narrower
"run your own" recipe (see #29 for the full node kit): the door and its gates,
not station content/buildout.

The door is Caddy. `caddy/Caddyfile` holds the routing/handlers; `scripts/token.sh`
manages credentials and generates `caddy/tokens.caddy` (gitignored, bearer-equivalent).
Gates: `scripts/smoke.sh`, `scripts/query-gate.sh`, `scripts/claims.sh`.

## Prerequisites

- Docker + Docker Compose v2
- `curl`, `python3`, `bash`, `git`
- For `scripts/query-gate.sh` (fast, no stack): Docker daemon access and the pinned Caddy image

## Fresh clone: reach a gated stream with one granted token

From the repository root:

```bash
# 1. Configuration (local defaults are fine for the door path)
cp .env.example .env

# 2. Grant one credential (creates secrets/tokens.txt + caddy/tokens.caddy)
chmod +x scripts/*.sh scripts/*.js scripts/*.py 2>/dev/null || true
./scripts/token.sh add you

# 3. Start the stack (brings up the door and its upstream services)
docker compose up -d

# 4. Verify the door and gates
./scripts/smoke.sh
```

`scripts/smoke.sh` reads `.env` (`PUBLIC_URL`, `FRONTEND_URL`) and `secrets/tokens.txt`.
With local defaults it probes `http://localhost:8090` against the frontend origin.
The camera/Mario groups are skipped automatically when their data is absent.

To print the invitation/camera URLs for the granted credential (sensitive):

```bash
./scripts/token.sh reveal you
```

No new service, no database, and no extra restart step are required.

## Door seams (parameterization)

Defaults reproduce the radio's current behavior. Override only when repointing the
door at a different layout.

| Seam | Default | Purpose |
|---|---:|---|
| `FRONTEND_URL` | `http://localhost:8090` | Frontend origin for redirects and CORS |
| `ICECAST_UPSTREAM` | `icecast:8000` | Audio/status upstream Caddy proxies to (`{$ICECAST_UPSTREAM}` in `caddy/Caddyfile`) |
| `MEDIAMTX_UPSTREAM` | `mediamtx:8889` | Camera WHEP upstream (`{$MEDIAMTX_UPSTREAM}` in `caddy/Caddyfile`) |
| `SITE_ADDRESS` / `PUBLIC_URL` / ports | local defaults | Listener address and published ports |
| `DOOR_TOKEN_FILE` | `$ROOT/secrets/tokens.txt` | `token.sh` credential store path |
| `DOOR_MATCHER_FILE` | `$ROOT/caddy/tokens.caddy` | `token.sh` generated matcher path |
| `DOOR_COMPOSE` | `$ROOT/docker-compose.yml` | Compose file used by `token.sh` reload/validation |
| `CADDY_IMAGE` | derived from compose | Caddy image used by `token.sh` standalone validation |
| `window.RADIO_BACKEND_URL` | `location.origin` | Frontend backend-origin override (see below) |

`ICECAST_UPSTREAM`/`MEDIAMTX_UPSTREAM` are docker-network targets; change them only
when repointing the door at different Icecast/MediaMTX services.

`DOOR_MATCHER_FILE` must live where `caddy/Caddyfile` can `import tokens.caddy` from
(relative to the config directory). A split layout requires adjusting the Caddyfile
import accordingly.

## Frontend/backend origin seam

`web/js/00-backend.js` (committed empty) is loaded before `web/js/00-config.js`.
`BACKEND_URL` defaults to `location.origin`, so a door-served frontend needs no
configuration.

For split deployments where the page and stream live on different origins (e.g.
Cloudflare Pages frontend + tunnel backend), inject the backend origin at deploy
time:

```bash
./scripts/inject-backend-origin.sh https://your-backend-origin
```

This writes `window.RADIO_BACKEND_URL` into `web/js/00-backend.js` (a deploy-time
working-tree change). The door path does not call it.

## Standalone gate checks

```bash
./scripts/query-gate.sh   # token/query never crosses the door (fast, no stack)
./scripts/claims.sh       # production-origin seams intact
./scripts/token.sh check  # config/permissions/lifecycle checks (may warn, must not fail fatal)
```

## Notes

- `caddy/tokens.caddy` and `secrets/tokens.txt` are bearer-equivalent secrets
  (gitignored, mode 600). Treat revealed links as secrets.
- The credential exchange redirects to `FRONTEND_URL`; on `.onion` hosts the door
  serves an audio-only response (no clearnet redirect).
- Revocation: `./scripts/token.sh rm <name>` (then reload happens automatically).
- This recipe covers the door, not the full station (content generation, camera,
  Mario, Tor hardening, etc.).
