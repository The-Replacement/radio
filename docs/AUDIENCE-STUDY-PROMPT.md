# Probabilistic Audience Study — Prompt & Findings

## Part 0 — The study (scanned from the project)

### What this project actually is (the facts, not the aspiration)

- **One circle, one laptop in the Sierras de los Comechingones.** Token-gated, unlisted, no directory announce. Current audience = the people who hold a token. Estimated 5–30 active listeners.
- **Content is 100% own-generated.** indiegenerator (deterministic, offline, seeds → tracks, −16 LUFS / ≤−1.5 dBTP / MP3 128 gates). $0 royalties. ~10 tracks per cycle, 6 h cadence, ~7 min render.
- **Two public surfaces exist:** `radio.daza.ar` (Cloudflare Pages, no door — serves the whole player + About to anyone) and the GitHub repo (open source). Everything else is behind Caddy's 404.
- **Growth channels already verified as aligned:** Tor `.onion` door, lumbung timesharing (Liquidsoap `input.http`), OpenStudio live voices, Funkwhale/Navidrome catalog, TinyIce transport. All in `docs/CHANNELS.md`.
- **Growth channels that are anti (break the spirit):** SaaS hosts, mainstream platforms (Twitch/YouTube/Spotify), AI DJ services, CDN-dependent delivery, public fediverse announce, shortwave pirate FM.
- **The "Ever growing" section** of README and the About page promise: more generators, voices, noises on the same stream. The door does not change; what comes through it does.

### The probabilistic model

Traditional audience metrics (listeners, streams, plays) are either unmeasurable (no tracking) or spirit-violating (analytics). So the model is over **circles**, not listeners.

```
dN/dt = N × r,   where  r = sharing_rate × resonance × conversion

N  = number of independent circles running this stack (or a fork)
L  = listeners per circle (closed, social-graph-capped)
r  = monthly growth rate of new circles
```

**Factor estimates (from project characteristics, not from data — there is no data by design):**

| Factor | Range | Basis |
|---|---|---|
| `sharing_rate` — P(a listener shares an invitation link/month) | 0.05–0.15 | Excited listeners share with 1–3 close friends; trust required |
| `resonance` — P(recipient resonates with the ethos) | 0.03–0.35 | Depends on segment (see table below) |
| `conversion` — P(resonant recipient → joins or founds a circle) | 0.01–0.30 | High barrier to found; lower to join |

**Resonance by segment:**

| Segment | P(resonance) | Global size (est.) | Alignment |
|---|---|---|---|
| Generative/algorithmic music makers | 0.15–0.25 | 50k–200k | High — they understand seeds, gates, determinism |
| Self-hosters / DIY infra | 0.10–0.20 | 500k–2M | High — docker compose, no SaaS, open source |
| Community radio enthusiasts | 0.08–0.15 | 100k–500k | High — they understand the radio ethos |
| Academic / researcher | 0.20–0.35 | 10k–50k | High but small — determinism, generative music |
| Privacy advocates | 0.05–0.15 | 5M–20M | Medium — value privacy, may not care about music |
| Underground/alt music fans | 0.03–0.08 | 10M–50M | Medium — value alternative, may not value DIY |
| The-Replacement ecosystem | 0.30–0.50 | 100–1k | Very high but tiny — already aligned |

**Probabilistic growth scenarios (circles, then listeners at L=10–30/circle):**

| Scenario | r/month | Circles @12mo | Circles @24mo | Circles @60mo | Listeners @60mo |
|---|---|---|---|---|---|
| Pessimistic | 0.02 | 1.3 | 1.6 | 3.3 | 33–99 |
| Base | 0.05 | 1.8 | 3.3 | 20 | 200–600 |
| Optimistic | 0.08 | 2.6 | 6.8 | 109 | 1k–3k |

**The honest read:** Even in the optimistic case, this is hundreds to low-thousands of listeners over 5 years. That is not "growth" in the platform sense. It is growth in the **movement** sense: each circle is a node that can itself grow, timeshare, and recruit. The compounding is in nodes, not in listeners.

**The real growth lever is node multiplication, not listener accumulation.** A listener who becomes a node (runs their own instance, generates content, timeshares) multiplies reach exponentially. A listener who only listens is a dead end in the graph. So the metric that matters is **nodes created per circle per year**, not listeners per circle.

### The tension (the thing the user is actually asking about)

Growth traditionally requires: discoverability, marketing, virality, open access, analytics.
Spirit requires: closed circle, no directories, no tracking, no algorithms, privacy-first, anti-corporate.

These are not just in tension — they are **structurally opposed**. You cannot have both "no discoverability" and "more discoverability." The resolution is not a compromise (that violates both). The resolution is a **reframe**: growth happens through **depth and node multiplication**, not through **breadth of listeners**.

Impact ≠ reach. Impact = cultural statement + depth of engagement + nodes created.

---

## Part 1 — The prompt (reusable)

Copy everything below the line into any model/agent to run a critical audience-reach audit.

---

```
You are auditing a private, token-gated radio's AUDIENCE REACH — not its code,
not its security, not its UI. The question is: how does this thing grow without
becoming the thing it opposes?

## The spirit you must protect (constitution)

Before judging anything, internalize what this thing IS:

1. The door is the philosophy. No credential → 404, never 401. The radio
   does not confirm that it exists. Caddy is the only door; Icecast and
   Liquidsoap publish no ports.
2. It is one station, one signature. Five lanes (motorik, kosmische, ambient,
   dub-electronic, indie-noise), one motif bank, one palette.
3. Everything is born from a seed. Deterministic: same (preset, seed) → same
   plan → same bytes. Re-listening is re-running the same number.
4. It never restarts itself. Tracks installed by hand; gates decide
   (−16 LUFS, ≤ −1.5 dBTP, MP3 128). Silence (mksafe) beats a crash.
5. Built agenticly, with AI, on the n3rv harness — SDD slices, binary gates.
6. Two credentials per person: token (once, in invitation link) + cookie
   (afterwards). Token stays out of address bar and logs.
7. OWN CONTENT ONLY. No samples, no third-party audio, $0 royalties.
   Anti-corporate: no algorithms, no ads, no ToS, no listener caps, no CDN.
8. Privacy-first. No tracking, no analytics, no third-party scripts.
9. DIY / artisanal. Laptop in the Sierras. Hand-installed. Open source.
10. Ever growing: more generators, voices, noises on the same stream. The door
    does not change; what comes through it does.

Every finding and every proposal must be measured against this constitution.
A "growth hack" that violates a principle is a regression, no matter how
effective it is. Severity is judged by SPIRIT DAMAGE FIRST, growth second.

## Scope

| Axis | What to examine |
|---|---|
| **Audience definition** | Who is this for? Not "everyone" — who resonates? What is the probability distribution of resonance across segments? |
| **Growth channels** | Which channels exist that do NOT violate the spirit? What is the probability each works? What is the expected reach per channel per year? |
| **Anti-channels** | Which channels would grow reach but violate the spirit? Name them explicitly so they are never accidentally adopted. |
| **Node multiplication** | How does a listener become a node (runs their own instance)? What is the barrier? How can it be lowered without violating spirit? |
| **The public surface** | `radio.daza.ar` (no door) is the ONE public surface. What does it currently say? What SHOULD it say to maximize resonance without marketing? |
| **Word of mouth** | The invitation link is the only distribution mechanism. What makes someone share it? What makes someone NOT share it? |
| **Timesharing / federation** | lumbung timesharing, OpenStudio live voices — these expand content, not audience directly. Do they indirectly grow audience? How? |
| **The seed as currency** | "Re-listening is re-running the same number." Can seeds be shared without opening the door? Is "seed 1042, motorik" a growth vector? |
| **Catalog companions** | Funkwhale/Navidrome on-demand listening. Does this grow reach or just serve existing listeners? |
| **Alternative transports** | Tor .onion, I2P. Do these reach new audiences or just serve privacy-conscious existing ones? |
| **The generator as movement** | indiegenerator is open source. Does "generate your own music" recruit more builders than "listen to our radio" recruits listeners? |

## Method

1. **Read the actual files first.** Cite every claim as file:line. No finding
   without evidence.
2. **Think probabilistically.** For every channel, estimate P(success) and
   expected reach. Use the resonance table from the study as priors. State
   your assumptions.
3. **Separate "reach" from "impact."** A channel that reaches 10,000 people
   who forget in 3 seconds has less impact than a channel that reaches 10
   people who become nodes. Argue which matters more for THIS project.
4. **Question the implicit growth model.** Is "more listeners" even the right
   metric? Or is "more nodes" the right metric? Or is "more seeds shared"
   the right metric? Define the metric before optimizing for it.
5. **Attack the trust boundary.** Growth requires trust. The door requires
   trust. How does trust scale? Does it scale at all? Is "doesn't scale" a
   bug or a feature?
6. **Weigh every hard number.** The 6 h cycle, 10 tracks/batch, 5 lanes,
   ~7 min render — these constrain content velocity. Does content velocity
   limit growth? Should it?

## Output format

**A. Verdict (≤10 lines).** What is this radio's realistic growth ceiling,
and is that ceiling a problem or a feature?

**B. Findings table.** `ID | severity (spirit-breaking / major / minor) |
axis | what | why it matters | P(success) estimate`.

**C. Deep dives** on the top 5–8 findings: evidence, the user-visible
consequence, the minimal change that respects the constitution.

**D. The reimagining.** Propose how the project could drastically increase
impact (not reach) while staying alternative. For each idea: what it changes,
what it costs, which constitutional principle it amplifies, and the
probability it works.

**E. Anti-patterns list.** Explicitly name the growth tactics that must
NEVER be adopted, with the constitutional principle each violates.

**F. A prioritized backlog** — slices, n3rv-style: each small, gated,
testable, with an explicit gate definition. Include a "node conversion" gate
(what proves a listener became a node?).

## Rules

- Do not propose: frameworks, build steps, a database, an SPA, a redesign by
  committee, or anything that adds a restart. This radio does not restart.
- Do not propose: analytics, tracking, SEO, social media marketing, paid
  promotion, public directories, open access, removing the door.
- Do not flatten the tone. The comments, the copy, and this audit should all
  sound like the same station.
- If the code is right and the spirit is only under-communicated, say that
  plainly — do not invent work.
- If "more listeners" is the wrong goal, say so and define the right goal.
```

---

## Part 2 — What you can do (actions that grow reach without violating spirit)

### Tier 1 — Do now (low effort, high alignment)

1. **Rewrite the About page as a manifesto, not a spec.** The public page
   (`radio.daza.ar`, no door) currently explains how the radio works. It should
   explain WHY it exists. "Why does this radio exist?" is more compelling than
   "How does this radio work?" The seed-on-stage feature (S11) already makes
   determinism visible — lean into it. Make the reader want to be part of the
   circle, not understand the technology.

2. **Make seeds shareable.** "Seed 1042, motorik" is a cultural artifact. It
   creates curiosity without opening the door. A listener who posts "listening
   to seed 1042" on a forum creates intrigue. The recipient cannot hear it
   without an invitation, but they can ask. The door stays closed; curiosity
   does the work. P(success) ≈ 0.40–0.60.

3. **Enable the Tor door.** Reaches privacy-conscious audiences without
   violating any principle. Needs the redirect fix (serve player over .onion
   or make it audio-only). P(success) ≈ 0.40–0.60. Effort: small.

4. **Cross-pollinate via The-Replacement.** Other satellite projects in the
   framework can reference the radio. The framework already connects them via
   project board and issue tracking. A mention in another satellite's README
   is not marketing — it is ecosystem coherence. P(success) ≈ 0.40–0.60.

### Tier 2 — Do next (medium effort, high alignment)

5. **Timesharing (lumbung).** Other nodes' streams land on ours via Liquidsoap
   `input.http`. This expands content diversity, which attracts through
   quality. Each timesharing partner is a potential new circle. The content
   becomes the growth engine, not marketing. P(success) ≈ 0.50–0.70.

6. **Live voices (OpenStudio).** When a guest does a live show, they bring
   their own audience. The show IS the invitation. The door stays closed; the
   guest opens it for their people. This is the highest-impact growth event
   available. P(success) ≈ 0.60–0.80. Effort: medium (Opus→MP3 integration).

7. **Make the generator itself the recruitment tool.** indiegenerator is open
   source. People who run it generate their own music. Some will want to
   broadcast it. The radio becomes the natural destination for generated music,
   not the other way around. "Generate your own music" recruits builders;
   builders become nodes. P(success) ≈ 0.30–0.50. Effort: medium (docs, examples).

8. **Catalog companion (Funkwhale/Navidrome).** On-demand listening of the
   generated library. This is a parallel surface that does not change the door
   but expands access to the content. It serves existing listeners better and
   may attract on-demand listeners who later want the live experience.
   P(success) ≈ 0.30–0.50. Effort: small–medium.

### Tier 3 — Do later (high effort, strategic)

9. **Node kit.** A documented, tested path for someone to go from "I heard
   about this" to "I'm running my own circle." Not a product — a recipe.
   `docker compose up` + `token.sh add` + `indiegenerator` + a README that
   explains the ethos. The barrier to node creation is the main bottleneck.
   Lowering it (without lowering the ethos) is the highest-leverage action.
   P(success) ≈ 0.20–0.40. Effort: high.

10. **Federation without centralization.** Not a protocol — a practice.
    Circles that timeshare occasionally. Circles that share seeds. Circles that
    reference each other. No central registry, no directory. Just nodes that
    know about other nodes through human connection. P(success) ≈ 0.10–0.30.
    Effort: high (cultural, not technical).

---

## Part 3 — Where you can do research

### Internal (no tracking, privacy-respecting)

| Source | What it tells you | How to access |
|---|---|---|
| Caddy access logs | How many unique IPs hit the public page (`radio.daza.ar`) | `docker compose logs caddy` — IP + path + status, no cookies |
| Icecast listener count | Peak concurrent listeners on the stream | `/status-json.xsl` (gated) — already polled by the player |
| GitHub repo metrics | Stars, forks, clones of radio + indiegenerator | GitHub API / repo insights |
| The-Replacement project board | Cross-satellite activity, SDD phase progress | `gh project` CLI |
| Deploy log | How often the backend updates, what changed | `data/deploy.log` on the box |
| Indiegenerator state | How many tracks generated, gate pass rates, cycle cadence | `data/indiegen/state.json` |

**Key constraint:** All of these are aggregate or self-reported. None track
individuals. That is the point. If you need per-user analytics, you have
already lost the spirit.

### External (research, not tracking)

| Source | What it tells you | URL |
|---|---|---|
| Community radio research (NFCB, Jacobs Media) | How community radio stations grow audiences | nfcb.org, jacobsmedia.com |
| Underground music scene (NTS, Rinse, Bandcamp Daily) | What alternative music audiences care about | nts.live, rinseradio.com |
| Privacy-first tools adoption (Tor, self-hosting) | Where privacy-conscious people gather | privacyguides.org, selfhosted.show |
| Generative/algorithmic music communities | Who makes their own music, who wants to broadcast it | lines (monome), algorithmic.art |
| Word-of-mouth research (academic) | How closed communities grow organically | search: "word of mouth growth closed community" |
| Open source project growth (GitHub Octoverse) | How OSS projects attract contributors without marketing | github.com (Octoverse reports) |
| The-Replacement ecosystem | Cross-pollination between satellites | github.com/The-Replacement |

### What you CANNOT research (and should not try)

- Individual listener behavior (no tracking, by design)
- Listener demographics (no analytics, by design)
- "Conversion funnels" (no funnel, by design — the door is the funnel)
- A/B testing (no experiments on listeners, by design)
- Social media engagement (no social media presence, by design)

If you feel the need for any of these, re-read the constitution. The absence
of these tools IS the product.

---

## Part 4 — How to make impact AND be alternative at the same time

### The reframe

Impact ≠ reach. Reach is "how many people heard it." Impact is "how many
people changed because of it." In the platform world, these are conflated
because platforms measure reach and call it impact. In this project, they
are deliberately decoupled.

**The five impact vectors (none require more listeners):**

1. **The door as the brand.** "No credential, 404" is not a limitation — it
   is the identity. It is what makes this different from every other radio.
   The door IS the marketing. People talk about things that are exclusive,
   not things that are ubiquitous. P(impact) ≈ 0.70–0.90.

2. **Seeds as currency.** Instead of sharing links (which leak), share seeds.
   "Seed 1042, motorik." This creates curiosity without opening the door.
   The recipient must be invited to hear it. The seed becomes a cultural
   artifact, a conversation starter, a reason to ask for an invitation.
   P(impact) ≈ 0.40–0.60.

3. **Quality as the growth engine.** The music is deterministic, gated, $0
   royalties. The quality speaks. People share things that are genuinely good
   and genuinely different. The gates (−16 LUFS, ≤−1.5 dBTP, MP3 128) are
   not just quality control — they are the brand promise. "This music passed
   the gates" is a statement. P(impact) ≈ 0.50–0.70.

4. **Live shows as growth events.** When a guest does a live show (OpenStudio),
   they bring their own audience. The show is the invitation. The door stays
   closed; the guest opens it for their people. This is the highest-impact
   growth event available because it converts a listener's social graph into
   new listeners without any marketing. P(impact) ≈ 0.60–0.80.

5. **The generator as the movement.** indiegenerator is open source. The
   movement is not "listen to our radio" but "generate your own music,
   broadcast it your way." The radio is the proof of concept. People who
   generate their own music are more likely to become nodes than people who
   only listen. The generator recruits builders; builders become nodes.
   P(impact) ≈ 0.30–0.50.

### The anti-patterns (NEVER adopt these)

| Tactic | Constitutional principle violated | Why it's tempting |
|---|---|---|
| Public radio directories (YP, dir.xiph.org) | #1 (unlisted), #10 (door) | "Free discovery" |
| SEO / noindex removal | #1 (unlisted), #8 (privacy) | "People can find us" |
| Social media presence (Twitter, Instagram) | #8 (privacy), #7 (anti-corporate) | "Reach" |
| Paid promotion / ads | #7 (anti-corporate), #9 (DIY) | "Scale" |
| Analytics / tracking (GA, Plausible) | #8 (privacy) | "We need to know our audience" |
| Open access (remove the door) | #1 (door), #6 (credentials) | "More listeners" |
| Listener caps / tiered access | #7 (no caps) | "Monetization" |
| CDN for assets (beyond hls.js vendoring) | #9 (no CDN), #8 (privacy) | "Performance" |
| Algorithmic recommendation | #7 (no algorithms) | "Engagement" |
| User accounts / registration | #6 (token+cookie), #8 (privacy) | "Personalization" |
| Public fediverse announce | #1 (unlisted) | "Community" |
| Shortwave pirate FM | Legal (PLAN-LEGAL-AR.md) | "Reach" |

### The honest conclusion

This project's growth ceiling, with spirit intact, is probably **hundreds to
low-thousands of listeners and dozens of nodes over 5 years.** That is not
a failure — it is the nature of a closed, artisanal, privacy-first system.
The impact is not in the numbers. It is in the statement: "this is possible.
You can build a radio that is yours, that plays music you generated, that
nobody can find unless you invite them, that never restarts, that tracks
nobody, that costs nothing in royalties."

The question is not "how do we grow?" The question is "how do we make the
statement louder without making the door wider?" And the answer is: through
quality, through seeds, through live shows, through the generator, and through
the door itself — which is the most interesting door on the internet because
it is the only one that says "404" when you knock without a key.

---

*Generated from a scan of the radio project (2026-11-10). All claims cite the
project's own documentation. No external data was used — by design, because
the project has no external data.*
