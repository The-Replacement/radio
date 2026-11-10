# Prompt: Deep audit of the radio — UI, communication, construction

You are auditing a **private, token-gated radio** that broadcasts from a laptop
in the Sierras de los Comechingones. Not a product review. A hunt for the
places where its *inner core spirit* leaks, contradicts itself, or fails to
reach the person on the other side of the screen.

## The spirit you must protect (constitution)

Before judging anything, internalize what this thing *is*:

1. **The door is the philosophy.** No credential → 404, never 401. The radio
   does not confirm that it exists. Caddy is the only door; Icecast and
   Liquidsoap publish no ports.
2. **It is one station, one signature.** Five lanes (motorik, kosmische,
   ambient, dub-electronic, indie-noise), one motif bank, one palette.
3. **Everything is born from a seed.** Deterministic: the same `(preset, seed)`
   → the same plan → the same bytes. Re-listening is re-running the same
   number.
4. **It never restarts itself.** Tracks are installed by hand; gates decide
   (−16 LUFS, ≤ −1.5 dBTP, MP3 128). Silence (`mksafe`) beats a crash.
5. **It was built agenticly, with AI, on the n3rv harness** — SDD slices,
   binary gates, comments that explain *why*, not *what*.
6. **Two credentials per person:** a token (once, in the invitation link) and
   a cookie (afterwards), so the token stays out of the address bar and logs.

Every finding and every proposal must be measured against this constitution.
A "fix" that violates a principle is a regression, no matter how modern it is.

## Scope

| Axis | What to examine |
|---|---|
| **UI** | `web/index.html` — the single page: layout, states, copy, accessibility, the About sections, the camera box, `?debug=1` |
| **Communication** | Every wire and every word: browser↔Caddy gate, cookie/token exchange, `status-json.xsl` polling, WebRTC/WHEP signaling, HLS fallback, Liquidsoap→Icecast, cam→MediaMTX→browser, cross-origin split (`radio.daza.ar` frontend / `stream.daza.ar` backend) |
| **Construction** | `docker-compose.yml`, `caddy/Caddyfile`, `liquidsoap/radio.liq`, `cam/capture.sh`, `scripts/*`, docs drift (README vs UI vs config), the shape of the code itself |

## Method

1. **Read the actual files first.** Cite every claim as `file:line`. No
   finding without evidence; no advice you did not ground in the code.
2. **Trace each user journey end to end**, including its failure modes:
   invitation link → cookie → play → stream cut → reconnect → 404-after-expiry;
   camera present/absent/WebRTC-blocked/HLS-only; empty `music/`; tunnel down.
3. **Look for contradictions between layers** — this repo is bilingual and
   multi-era, so watch for: Spanish vs English strings and comments in the same
   file, duplicated logic (e.g. two parallel 15 s polls touching the same
   DOM node), state shown in two places that can disagree (dot vs text vs
   banner vs button), hardcoded values duplicated between frontend and config,
   README promises vs runtime reality.
4. **Question the implicit state machine.** The player has live / stopped /
   reconnecting / error / autoplay-blocked / credential-lost, expressed via a
   dot, a label, a banner, an `aviso`, a `tel`, and a button icon. Map it
   explicitly; find the states that are unreachable, ambiguous, or
   contradictory, and the transitions with no user-visible feedback.
5. **Attack the trust boundary.** Where does the token travel (URL, cookie,
   SDP POST, m3u8 path, logs)? Is the README's promise ("token out of the
   address bar and out of the logs on every playback") actually true for every
   request the page makes? What does Caddy log? What happens on expiry, on
   replay, cross-origin?
6. **Weigh every hard number.** `MAX_REINTENTOS=5`, `800ms * n` backoff, 3-tick
   watchdog, 15 s polls, `liveSyncDuration: 1.5`, 6 s WebRTC timeout, 2 s ICE
   wait — each was measured and documented. Do not suggest replacing them;
   ask whether they belong in one place, and what happens when they collide.

## Output format

**A. Verdict (≤10 lines).** What is this radio's current state of soul —
where the spirit is strong, where it is muted.

**B. Findings table.** `ID | severity (spirit-breaking / major / minor) |
file:line | axis | what | why it matters`.

**C. Deep dives** on the top 5–8 findings: evidence, the user-visible
consequence, the minimal fix that respects the constitution.

**D. The reimagining (the point of this exercise).** Propose how the UI and
the communication could *drastically* express the core spirit instead of
merely containing it. Examples of the level expected — take them as a
direction, not a checklist:
- The player could show **what is playing as a seed, not just a title**:
  lane, seed, plan hash — making "re-listening is re-running the same number"
  visible rather than documented.
- The **states could speak in the station's voice**: silence is a valid
  state, not an error; "the door" (expired credential) could be a ritual, not
  a stack trace.
- The **About sections duplicated from the README** could become one source
  of truth, so the station's story can never drift from the station.
- The **listener count** could express *circle*, not *analytics*.
- The **camera** (mute video, audio is the radio's) is a strong aesthetic
  statement — is it presented as one?
For each idea: what it changes, what it costs, and which constitutional
principle it amplifies.

**E. Construction verdict.** Is the 622-line single-file page the right
shape for this project's ethos (readable whole, no build step) or has it
outgrown it? Argue both sides and conclude. Same for the compose file, the
scripts, and the docs.

**F. A prioritized 30/60/90-day-ish backlog** — slices, n3rv-style: each one
small, gated, testable, with an explicit gate definition.

## Rules

- Do not propose: frameworks, build steps, a database, an SPA, a redesign by
  committee, or anything that adds a restart. This radio does not restart.
- Do not flatten the tone. The comments, the copy, and this audit should all
  sound like the same station.
- Severity is judged by *spirit damage first, engineering second*.
- If the code is right and the spirit is only under-communicated, say that
  plainly — do not invent work.
