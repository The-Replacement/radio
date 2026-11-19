# Gates: inventory, prerequisites, and fast vs. integration split

This document is the single reference for every gate in the radio project: what
it checks, whether it needs the live stack, what it needs installed, where it
runs, and its exit code. It exists so a contributor (or an agent) can pick the
right gate for a change without guessing.

Last updated: 2026-11-19.

## Fast vs. integration

- **Fast gates** run without the radio stack. They are safe in CI and pre-commit.
- **Integration gates** need a running stack (Icecast, Liquidsoap, Caddy, and
  sometimes camera/Mario). They run on the box or in a full compose environment.

A gate's "fast/integration" label is fixed; do not relabel a gate to make a
change pass. If a change only touches docs or Python, the fast gates are enough.
If it touches compose, Caddy, Liquidsoap, or the door, run the integration
gates too.

## Prerequisite checklist (install once)

| Tool | Used by | Install |
|---|---|---|
| `ruff` | CI, pre-commit | `pip install ruff` |
| `mypy` | CI | `pip install mypy` |
| `pytest` | CI | `pip install pytest` |
| `shellcheck` | CI (shellcheck job) | `apt install shellcheck` |
| `node` + `npm` | `ui-gate.js`, `docs.sh` | `apt install nodejs npm` |
| Chrome/Chromium | `docs.sh` | `apt install chromium` |
| `docker` + `docker compose` | `query-gate.sh`, `ac1-soak.sh`, integration gates | Docker install |
| `curl` | `smoke.sh`, `prod-check.sh` | `apt install curl` |
| `python3` | `query-gate.sh`, `mypy` | `apt install python3` |
| `ffmpeg` | camera probe (optional) | `apt install ffmpeg` |
| `.env` + `secrets/tokens.txt` | `smoke.sh`, `prod-check.sh`, `token.sh` | copy from `.env.example`, run `token.sh add` |

## Gate inventory

### Fast gates (no stack required)

| Gate | Command | Prerequisites | Where it runs | Exit |
|---|---|---|---|---|
| `ruff check` | `ruff check .` | ruff | CI, pre-commit, local | 0 = clean |
| `mypy` | `mypy .` | mypy, Python 3.11 | CI, local | 0 = clean |
| `pytest` | `pytest` | pytest | CI, local | 0 = all pass |
| shellcheck | `shellcheck -x` (recursive) | shellcheck | CI | 0 = clean |
| `ui-gate.js` | `node scripts/ui-gate.js` | node | CI (optional), local | 0 = pass |
| `claims.sh` | `scripts/claims.sh` | git | CI, local | 0 = pass |
| `docs.sh` | `scripts/docs.sh` | node, npm, Chrome/Chromium | local | 0 = pass |
| `query-gate.sh` | `scripts/query-gate.sh` | docker, python3, Caddy image | local (container) | 0 = pass |
| `prod-check.sh` | `scripts/prod-check.sh` | `.env`, `secrets/tokens.txt` | box, local | 0 = pass |
| `token.sh check` | `scripts/token.sh check` | `.env`, `secrets/tokens.txt` | box, local | 0 = pass |

### Integration gates (running stack required)

| Gate | Command | Prerequisites | Where it runs | Exit |
|---|---|---|---|---|
| `smoke.sh` | `scripts/smoke.sh` | caddy on `BASE`, `secrets/tokens.txt`, `FRONTEND` from `.env`, curl; optional ffmpeg/camera/host | box, full compose | 0 = pass |
| `ac1-soak.sh` | `scripts/ac1-soak.sh` | docker, liquidsoap logs | box | 0 = pass |
| `spacer-race.sh` | `scripts/spacer-race.sh` | docker, liquidsoap | box | 0 = pass |

## Skip conditions in `smoke.sh`

- **Camera group** (`scripts/smoke.d/20-auth-and-media.sh`): skipped if
  `data/cam/index.m3u8` is absent or has no segments.
- **Mario/host group**: skipped if no `data/host/*.wav` exists.
- **Second door (group 0)**: always runs; documents the open-door exception
  (F-02).

## Where gates run

- **CI** (run in the separate infra/CI setup; this repo has no `.github/`): ruff, mypy, pytest, shellcheck (recursive).
- **Pre-commit / local**: ruff, mypy, pytest, shellcheck, ui-gate.js.
- **Box (manual or cron)**: smoke.sh, ac1-soak.sh, prod-check.sh, token.sh check.

## Exit-code convention

Every gate exits `0` on success and non-zero on failure, printing a human-readable
summary. `radio-job` subcommands follow the same convention, with deferral
reported as exit `0` (a deferral is a correct outcome, not a failure).

## Known inconsistency

`scripts/smoke.d/20-auth-and-media.sh` previously called `pasa`/`falla`/`codigo`/
`RAIZ`, which `scripts/smoke-lib.sh` did not define (Spanish aliases left over
from a partial rename). Resolved in #101: both sides now use `pass`/`fail`/
`status_code`/`ROOT`. If you see the old names again, the rename regressed.
EOF
echo "written"

## Maintenance (audit Y3/#84)

The gate/ops surface is justified by regressions and contracts, but carries
maintenance cost. Periodically review gates/features against active invariants:

- For each gate/script, record the regression or contract it protects (in PR/issue/docs).
- Retire or simplify gates that no longer protect a live invariant, keeping traceability.
- Prefer tightening existing gates over adding parallel ones; avoid duplicating checks across CI, hooks, and box scripts without a documented reason.
- When a feature path is removed, remove or update its gates in the same change.
