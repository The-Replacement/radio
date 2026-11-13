# Playout Contract — Mario ↔ Liquidsoap / Caddy / Web

This document defines the **single source of truth** for filename, path, and
alias conventions shared between Mario (producer) and its consumers
(Liquidsoap, Caddy, web player). Any change to these conventions must update
this document and the corresponding tests.

## 1. Host directory (`/host`, mounted from `./data/host`)

Mario writes all segments here. Liquidsoap reads from `/host`.

### Bucket marker
- **File:** `/host/current-bucket`
- **Content:** the current time bucket string (e.g. `2026-11-13T14`), UTF-8, no trailing newline.
- **Producer:** `gen._write_bucket_marker()` (atomic write + fsync).
- **Consumer:** Liquidsoap `host_bucket()` reads this file.

### Transition segments
- **Filename:** `{current_seed}_{next_seed}_{bucket}.wav`
- **Example:** `100_200_2026-11-13T14.wav`
- **Producer:** `render.cache_filename()` → `generate_segment()`
- **Consumer:** Liquidsoap `host_path = "/host/#{es}_#{ss}_#{bucket}.wav"`
- **Sanitization:** bucket is sanitized via `sanitize_bucket()` (colons → dashes, slashes → underscores).

### Weather segment
- **Cache filename:** `weather_{hash[:8]}_{bucket}.wav`
- **Playout alias:** `weather_{bucket}.wav` (symlink or copy to cache file)
- **Producer:** `weather.weather_cache_filename()` + `weather.weather_alias_filename()` → `generate_weather_segment()`
- **Consumer:** Liquidsoap `weather_path = "/host/weather_#{bucket}.wav"`

### RSS/news segment
- **Cache filename:** `rss_{cache_key[:8]}_{bucket}.wav`
- **Per-feed alias:** `rss_{slug}_{bucket}.wav`
- **Stable alias (playout reads this):** `rss_{bucket}.wav`
- **Fallback filename:** `rss_fallback_{slug}_{bucket}.wav`
- **Producer:** `rss.rss_cache_filename_from_key()`, `rss.rss_alias_filename()`, `rss.rss_stable_alias_filename()`, `rss.rss_fallback_filename()` → `generate_rss_segment()`, `generate_rss_fallback_segment()`
- **Consumer:** Liquidsoap `rss_path = "/host/rss_#{bucket}.wav"`

### Liner/ident segments
- **Cache filename:** `liner_{safe_ident_id}_{seed}_{bucket}.wav`
  - `safe_ident_id`: non-alphanumeric chars (except `-` and `_`) replaced with `_`.
- **Playout alias:** `liner_{seed}_{bucket}.wav` (symlink or copy)
- **Producer:** `liners.liner_cache_filename()` → `generate_liner_segment()`
- **Consumer:** Liquidsoap scans `liner_*_{seed}_{bucket}.wav` pattern in `/host`

### Bucket sanitization
- **Function:** `timebucket.sanitize_bucket(bucket)`
- **Rule:** replace `:` with `-`, replace `/` with `_`.
- **Example:** `2026-11-13T14:00` → `2026-11-13T14-00`

## 2. Music directory (`/music`, mounted from audio root)

- **Content:** audio files only (`*.flac`), no metadata.
- **Metadata:** catalog.json + sidecars live in meta_root (separate from audio root in split layout).
- **TrackMeta.path**: relative path within music dir (e.g. `motorik_steady_hand_s100.flac`). Used by web player to fetch sidecars via `/music/{path}`.

## 3. Caddy path mappings

| Caddy path | Root | Notes |
|------------|------|-------|
| `/host/*` | `/srv-host` | Mario segments (read-only) |
| `/music/*` | `/srv-music` | Sidecars (read-only) |
| `/cam/*` | `/srv-cam` | HLS segments |

## 4. Web player contract

- Status endpoint: `/status-json.xsl` (Icecast, proxied by Caddy, query stripped)
- Stream endpoint: `/stream` (Icecast, proxied by Caddy, query stripped)
- Camera HLS: `/cam/index.m3u8`, `/cam/{token}/index.m3u8`
- Camera WHEP: `/cam/whep` (POST, SDP)
- Music sidecar: `/music/{track_path}` (returns JSON with lane, seed, plan_sha256)
- Catalog: `/music/catalog.json`

## 5. Producer-consumer expectations (tests)

The following invariants must hold:
1. `sanitize_bucket("2026-11-13T14")` == `"2026-11-13T14"` (no change needed)
2. `sanitize_bucket("2026-11-13T14:00")` == `"2026-11-13T14-00"`
3. `cache_filename(100, 200, "2026-11-13T14")` == `"100_200_2026-11-13T14.wav"`
4. `weather_alias_filename("2026-11-13T14")` == `"weather_2026-11-13T14.wav"`
5. `rss_stable_alias_filename("2026-11-13T14")` == `"rss_2026-11-13T14.wav"`
6. `liner_cache_filename("station", 100, "2026-11-13T14")` == `"liner_station_100_2026-11-13T14.wav"`
7. Liquidsoap globs `liner_*_{seed}_{bucket}.wav` — matches cache filename pattern.
8. Liquidsoap reads `/host/rss_{bucket}.wav` — matches `rss_stable_alias_filename`.
9. Liquidsoap reads `/host/weather_{bucket}.wav` — matches `weather_alias_filename`.
10. Liquidsoap reads `/host/{es}_{ss}_{bucket}.wav` — matches `cache_filename`.