# Live Video Broadcast from Audio-Reactive Visualization — Design

**Status:** design (SDD: explore → propose → spec → design). No code written yet.
**Source idea:** `juanmanueldaza/limites-visualization` (p5.js + Web Audio AnalyserNode, 4 switchable line visualizations).
**Goal:** turn the radio's muted video channel (currently a USB webcam) into an audio-reactive visualization derived from the radio's own stream, without changing the door or the transport.

---

## 1. Why (constitutional alignment)

The radio's video is already muted — the audio is the radio's (README.md:13-14, 248). The camera exists because "in addition to listening you can watch" (README.md:13). Swapping the webcam for a visualization of the radio's own audio is more aligned, not less:

- **Own content only** (#7): the visualization is derived from our own stream, no third-party video.
- **Ever growing** (#10): "more generators, voices, noises on the same stream. The door does not change; what comes through it does." Visual noise is a new kind of content on the same door.
- **No CDN, no SaaS** (#9): rendered on the box, served by Caddy, gated by the same token.
- **Determinism adjacent** (#3): the visualization is a function of the audio, which is a function of the seed. Re-watching is re-running the same number through a new lens.

The door, the transport (MediaMTX WebRTC + HLS), the gate (Caddy `@gate`), and the frontend (`hayCamara`, `cam-caja`) do not change. Only the source feeding the `cam` path changes.

---

## 2. Current video pipeline (baseline)

```
USB webcam (MJPEG 640x480)
   |
   |  cam/capture.sh: ffmpeg v4l2 -> libx264 600k, 15 fps, g=15
   +--> RTSP (tcp) --> mediamtx:8554/cam --> WebRTC WHEP (sub-1s)
   |
   `--> HLS fmp4 segments --> ./data/cam --> Caddy --> HLS fallback (1.5s)
```

- Single door: Caddy gates `/cam/whep`, `/cam/*`, `/cam/<token>/*` (caddy/Caddyfile:77-182).
- MediaMTX path `cam` is `source: publisher` (mediamtx/mediamtx.yml:42-44): only ffmpeg writes.
- Frontend probes `/cam/index.m3u8` for freshness via `EXT-X-PROGRAM-DATE-TIME` (web/index.html:843-861); shows the box only if a segment is <10 s old.
- UDP WebRTC port bound to 127.0.0.1 (docker-compose.yml:55-61).

The visualization service replaces the webcam as the publisher of the `cam` path. The two are mutually exclusive at runtime (same path, same publisher slot). The operator picks one by running one service or the other.

---

## 3. Approaches considered

### A. Browser-side render (client does the viz)
Each listener's browser renders p5 from the audio stream. **Rejected:** no single door (video bypasses Caddy/MediaMTX), N× render cost, and the frontend already receives audio separately — it would duplicate work and break the "muted video, radio is the audio" contract (README.md:248).

### B. Headless Chromium service (full p5 fidelity)
A container runs headless Chrome rendering the p5 sketch, captures the canvas, pipes to ffmpeg → RTSP+HLS. **Pros:** exact reuse of limites-visualization assets. **Cons:** Chromium in a container (~1 GB), audio analysis must be fed into the browser (via a local audio source or by streaming the Icecast URL into a Web Audio node), and p5's `draw` loop must be captured at a stable frame rate. High effort, high fidelity.

### C. ffmpeg-native audio visualization (recommended first slice)
Use ffmpeg's built-in audio filters (`showfreqs`, `showspectrum`, `showwaves`, `avectorscope`) consuming the Icecast stream directly, encoding H.264 to RTSP+HLS. **Pros:** no new language, no browser, reuses the cam Docker image (Debian + ffmpeg), ~20 lines of capture.sh variant, real-time audio-reactive, gate-able immediately. **Cons:** less artistic than p5; limited to ffmpeg's filter vocabulary.

### D. Node canvas renderer
Port the p5 logic to `node-canvas`/`skia-canvas` with a server-side FFT (e.g. `node-web-audio-api` or ffmpeg-piped PCM). **Pros:** lighter than Chromium, full control. **Cons:** porting p5 math to a different canvas API, and audio capture plumbing. Medium effort, medium fidelity.

### Decision

**Ship C first** (minimal, aligned, gate-able), keep B/D as future enhancements. C is the only one that can be verified end-to-end with the tools already on the box (ffmpeg is there, the Icecast stream is on the internal network, mediamtx accepts RTSP). B and D are design space for later; C proves the pipeline and the door.

The limites-visualization idea is not discarded: its *concept* (audio-reactive lines keyed to frequency/time data) is what C reproduces with ffmpeg filters, and B/D remain the path to its exact aesthetic.

---

## 4. Recommended architecture (Approach C)

### 4.1 New service: `viz` (or reuse `cam` via mode)

Two options; recommend **option 2** for minimal frontend churn:

- **Option 1 — separate path `viz`:** new MediaMTX path, new Caddy handlers, new frontend probe. More surface, but webcam and viz can coexist.
- **Option 2 — same path `cam`, mode-selected (recommended):** a single video publisher slot fed by either `cam` (webcam) or `viz` (visualization). Operator runs one or the other; the frontend cannot tell the difference. Zero frontend change. The door, the path, the probe, the HLS fallback all reuse existing code.

### 4.2 Service definition (docker-compose)

```yaml
viz:
  image: ... # reuse cam image or a viz variant
  container_name: radio-viz
  restart: unless-stopped
  depends_on:
    - mediamtx
    - icecast
  environment:
    ICECAST_URL: http://icecast:8000/stream
    CAM_CLOCK: ${CAM_CLOCK:-0}
  volumes:
    - ./data/cam:/out          # same HLS dir -> frontend hayCamara works unchanged
    - ./cam/viz-capture.sh:/usr/local/bin/capture.sh:ro
  networks:
    - radio
```

`data/cam` is shared with the webcam path so the frontend's `/cam/index.m3u8` probe works without change.

### 4.3 capture script (viz-capture.sh)

Mirrors cam/capture.sh but the input is the Icecast stream and the encode uses an audio filter:

```bash
ffmpeg -hide_banner -loglevel warning -nostdin \
  -i "$ICECAST_URL" \
  -filter_complex "showfreqs=s=640x480:mode=line:ascale=log:fscale=log,format=yuv420p" \
  -r 15 -c:v libx264 -preset veryfast -tune zerolatency \
  -profile:v baseline -pix_fmt yuv420p \
  -b:v 600k -maxrate 700k -bufsize 1200k \
  -g 15 -keyint_min 15 -sc_threshold 0 \
  -an \
  -f rtsp -rtsp_transport tcp "rtsp://mediamtx:8554/cam" \
  -f hls -hls_time 1 -hls_init_time 1 -hls_list_size 6 \
  -hls_flags delete_segments+independent_segments+omit_endlist+temp_file+program_date_time \
  -hls_segment_type fmp4 -hls_fmp4_init_filename init.mp4 \
  -hls_segment_filename "$OUT/seg%03d.m4s" "$OUT/index.m3u8"
```

Notes:
- `-an` on output: the video carries no audio (the radio is the audio, as today).
- The Icecast stream is MP3 128; ffmpeg decodes it internally for the filter.
- `showfreqs`/`showspectrum`/`showwaves` are selectable via an env var `VIZ_MODE` (frequency spectrum, waveform, vectorscope) — same idea as limites-visualization's 4 modes, but ffmpeg-native.
- Program-date-time flag preserved so `hayCamara()` freshness check (web/index.html:843-861) keeps working.

### 4.4 Mode switching (operator-facing)

A single env knob in `.env`: `VIDEO_SOURCE=camera|visualization` (default `camera`). The compose runs the matching service; both share `./data/cam`. Documented in README "The camera" section. No frontend change.

---

## 5. Data flow

```
Icecast :8000/stream (MP3 128, internal network)
   |
   |  viz-capture.sh: ffmpeg decodes audio -> filter -> H.264
   +--> RTSP (tcp) --> mediamtx:8554/cam --> WebRTC WHEP (sub-1s)  [gated by Caddy /cam/whep]
   |
   `--> HLS fmp4 --> ./data/cam --> Caddy /cam/* --> HLS fallback (1.5s)  [gated]
```

Caddy, MediaMTX, the frontend, and the smoke test are untouched. The door does not change.

---

## 6. Edge cases

- **Icecast down / no music:** ffmpeg exits; the script retries with backoff (cam/capture.sh:99-103 pattern). The HLS index is deleted first so players don't hang on stale segments (cam/capture.sh:32-34).
- **Switching sources:** stop one service, start the other; both write to the same `./data/cam`. Clear the dir between switches to avoid stale segments. Documented.
- **WebRTC UDP:** unchanged (127.0.0.1 binding). Viz does not need new ports.
- **Log level:** ffmpeg `-loglevel warning`; no credentials in URLs (the Icecast stream is internal, no token).
- **CPU:** ffmpeg audio filter + x264 at 600k/15fps on a laptop is comparable to the webcam encode. Measured baseline: webcam encode is ~50 ms pipeline (README.md:312). Should be re-measured for the filter path.

---

## 7. Gates (binary, n3rv-style)

1. `scripts/smoke.sh` still passes (no credential → 404 on `/cam/*`, `/cam/whep`; with credential → 200). No change expected; verifies the door did not move.
2. `ffmpeg` consumes `http://icecast:8000/stream` and produces a non-empty `.m4s` segment within 5 s.
3. `./data/cam/index.m3u8` contains `#EXT-X-PROGRAM-DATE-TIME` on every segment (so `hayCamara()` freshness works).
4. A credentialed browser shows the `cam-caja` box and plays video within 3 s of page load.
5. WebRTC path: WHEP signalling returns 400 (invalid SDP) with credential, 404 without — already measured (smoke.sh:262-274).
6. Self-test: removing the `program_date_time` flag makes `hayCamara()` return false → box hidden → gate red. (Proves the gate measures freshness.)
7. CPU/latency measured and recorded (comparable to webcam baseline).

---

## 8. Risks

- **ffmpeg filter quality vs. p5 aesthetic:** C is utilitarian. If the operator wants the exact limites-visualization look, B or D is needed. Mitigation: ship C, then evaluate B as a separate slice.
- **Audio reactivity latency:** filters react to the decoded stream; expect ~1–2 s lag vs. the audio the listener hears (buffering). Acceptable for ambient viz; not for lip-sync (irrelevant here, video is muted).
- **Container image reuse:** the cam image has ffmpeg + fonts. If viz needs a different filter (e.g., `drawtext` for seed display), fonts are already there (DejaVu). No new image needed for C.
- **Option 2 coupling:** running viz instead of cam means no webcam. If both are wanted simultaneously, switch to Option 1 (separate path) later.

---

## 9. Future (B/D) — not in this slice

- **B (headless p5):** container with Chromium + the limites-visualization sketch, audio fed via the Icecast URL into a Web Audio `MediaElementAudioSourceNode`, canvas captured via `captureStream()`, piped to ffmpeg. Brings the exact 4-mode aesthetic and the seed-on-stage overlay (lane · seed · plan_sha256) onto the video.
- **D (node canvas):** lighter than B, requires porting p5 math.
- **Seed display on video:** render `plan_sha256` as text over the viz (ffmpeg `drawtext` in C, canvas text in B/D). Ties the video to the seed currency (AUDIENCE-STUDY-PROMPT.md:306-310).

---

## 10. Backlog slices (if approved)

| Slice | Change | Gate |
|---|---|---|
| V-1 | Add `viz` service + `viz-capture.sh` (Approach C, ffmpeg filters) | Gates 1–7 above |
| V-2 | `VIDEO_SOURCE` env knob + docs | Switching sources works; smoke green |
| V-3 | Selectable viz mode (spectrum/waveform/vectorscope) via `VIZ_MODE` | Each mode produces distinct output |
| V-4 | (future) Headless p5 service (Approach B) | Exact limites-visualization aesthetic on the stream |
| V-5 | (future) Seed text overlay on video | Seed visible in frame, matches `#now` |

---

## 11. Open questions for the operator

1. Do you want the viz to **replace** the webcam or **coexist** (Option 1 vs 2)?
2. Which ffmpeg filter aesthetic do you prefer as the default (spectrum bars, waveform, vectorscope)?
3. Is the headless-p5 (B) fidelity worth the Chromium weight, or is ffmpeg-native (C) enough?
4. Should the current track's seed be burned into the video frame?

---

*Design derived from: limites-visualization (p5/Web Audio), radio cam pipeline (cam/capture.sh, mediamtx/mediamtx.yml, caddy/Caddyfile), and AUDIENCE-STUDY-PROMPT.md (ever-growing principle). No source code written.*
EOF
echo "written"