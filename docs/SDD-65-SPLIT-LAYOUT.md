# SDD #65 — Split library layout (audio_root + meta_root)

**Status:** T0 locked, T1/T2 repo wiring prepared. **Rollout is gated on coordinated indiegenerator (external) changes and lockstep deploy.** Do not deploy partial state.

## Locked layout (radio concept)
- **`meta_root = ./music`** (library metadata): `catalog.json`, per-track `*.json` sidecars (`indiegen/catalog/1`, `indiegen/sidecar/1`), operator docs as applicable (e.g. `LEEME.txt`). Served at `GET /music/*` by Caddy and read by Mario (`MARIO_META_DIR=/music`, legacy `MARIO_MUSIC_DIR` compatible). Stable HTTP contract.
- **`audio_root = ./music-audio`** (Liquidsoap scan only): strictly `*.flac` plus audio-only staging (`.tmp`, tmpfs-covered in container). **Never** contains sidecars/catalog/docs; never served as `/music/*`; never used by Mario for metadata. Liquidsoap `playlist("/music")` scans this root (mounted to container `/music`).

## Coordinated publish contract (indiegenerator, required)
- Publish audio → `audio_root`, sidecars + `catalog.json` → `meta_root`.
- Per-root atomic staging/publish (temp+rename+fsync) and split managed cleanup: `*.flac` in `audio_root`, `*.json` + `catalog.json` in `meta_root`.
- Publish new files before deleting stale per root; write `catalog.json` last in `meta_root` to reduce inconsistent windows.
- `.tmp` staging/scanner hiding scoped to `audio_root` only.
- Target CLI/config: explicit dual-root mode (e.g. `--audio-dir` / `--meta-dir`), invoked by radio compose once supported.

## Lockstep deploy (via `INDIEGEN_CONTEXT`)
1. Update indiegenerator (`../indiegenerator`) with dual-root publish/rotation, staging, cleanup, tests, and docs.
2. Apply radio repo changes (T1–T3): compose mounts/env/args, `liquidsoap/radio.liq`, Mario meta-dir support, docs, migration.
3. Migrate existing library: partition current `./music` into `audio_root` (`*.flac`) and keep metadata in `meta_root` (`*.json`, `catalog.json`, `LEEME.txt`) preserving stems/integrity; ensure `./music-audio/.tmp` handling as needed.
4. Build/restart in order: `docker compose build indiegen` (and `mario` if needed) → `docker compose up -d` affected services → run one controlled cycle/trigger.
5. Verify on mainframe-like runtime:
   - **AC1:** zero non-audio `cannot decode` over sustained reload/watch/rotation window (>=60 min) with metadata present.
   - **AC2:** all `.flac` rotate; watch picks up added tracks without restart.
   - **AC3/AC4:** Mario determinism/catalog+sidecar behavior and frontend `/music/*` metadata rendering unchanged.
   - **AC5:** audio-empty `audio_root` => silence, no crash/leak/request-storm (2.4.6 safety).
   - **AC6:** `/music/*` remains gated; no CORS/door regressions.

## Notes
- Deletions from playlist still require `docker compose restart liquidsoap` (unchanged) unless separately addressed.
- `LEEME.txt` remains operator guidance in `meta_root` (not scanned). Keep empty-library silence guarantee documented.
- See n3rv-memory topics `sdd-separate-metadata-scanned-audio-tree-65-*` for full SDD artifacts.

## Retirement plan (compatibility paths, audit Y1/#80)

Compatibility logic exists to support split-layout migration and legacy callers:
`mario/config.py` (`music_dir`/`meta_dir` sync, `metadata_dir`), CLI flags
(`--music-dir`/`--meta-dir`), and catalog-first/sidecar fallback in track
loading.

**Removal criteria (all must hold):**
- Split layout rolled out on all deployed boxes (meta in `./music`, audio in
  `./music-audio`) and verified (AC1–AC6) for a sustained period.
- Indiegenerator publishes dual-root in production; no deployment relies on the
  legacy co-located layout.
- No scripts/docs/envs in active use depend on `MARIO_MUSIC_DIR` as the sole
  metadata selector where `MARIO_META_DIR` is required.

**Retirement checklist:**
1. Announce deprecation window in docs/changelog; prefer `MARIO_META_DIR` (and
   CLI `--meta-dir`) as the canonical metadata path.
2. Remove `music_dir`↔`meta_dir` implicit sync in `MarioConfig` once callers are
   migrated; keep explicit aliases only if needed for one release.
3. Drop sidecar-fallback paths only after catalog/sidecar publish is guaranteed
   by the coordinated contract.
4. Delete this section and update `docs/INDIEGENERATOR.md`/compose comments when
   legacy paths are removed.
