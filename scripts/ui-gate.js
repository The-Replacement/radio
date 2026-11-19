#!/usr/bin/env node
/*
 * ui-gate.js — measures the player's state structure.
 *
 *   ./scripts/ui-gate.js
 *
 *   ./scripts/ui-gate.js              runs the scenarios
 *   ./scripts/ui-gate.js --self-test  proves that the gate can fail
 *
 * web/index.html has no build and no test runner: this extracts its <script>,
 * runs it against a made-up DOM and fetch, and counts what the eye cannot see:
 * how many status fetches happen per tick, how many times #now is written, and
 * what the banner does while a reconnection chain is in flight.
 *
 * It does not touch the network, does not touch the camera, and needs no stack
 * running.
 *
 * Exit codes:
 *   0  every scenario passes
 *   1  at least one failure (or the self-test found a gate without teeth)
 *
 * Scenarios:
 *   S1  one tick: one fetch, one writer of #now, the chain outranks it.
 *   S1  the endpoint's two shapes (object/arrayVal) paint the same.  [F-15]
 *   S2  the 200 / 404 / network rejection matrix at load.         [F-03]
 *   S2  a transient failure does not turn the radio off.          [F-04]
 *   S2  the door's copy is a SINGLE one, and both paths that can
 *       discover the 404 write it.
 *   S3  the token leaves the address bar: replaceState on the decision,
 *       the t on requests only with the dead cookie, and nothing decided
 *       when the network did not answer.                          [F-01]
 *   S10 the credential poll is bounded: a request that never comes back
 *       does not freeze the chain — the abort reads as network and the
 *       chain reaches attempt 1 (the number) (F-09).
 *   F-17 a manual retry after the five attempts are spent starts with a
 *       fresh budget, not with the exhausted counter.
 *   F-18 the listener's hand outranks: the click cancels what is scheduled
 *       (one attempt in flight) and the chain does not revive a paused
 *       radio.
 *
 * Self-test (see issue #23): a green gate proves nothing unless it was seen
 * failing. It breaks the code in memory —one guard per mutation— and demands
 * reds on each one, and greens on the real page. If a mutation leaves the
 * gate green, the gate stopped measuring that: it comes out red too.
 */
'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const ROOT = path.join(__dirname, '..');
const PAGE = path.join(ROOT, 'web', 'index.html');

// Ids the page really declares. The gate's made-up DOM only answers those:
// testing against an id that does not exist is testing against nobody.
const IDS = new Set();

/* --------------------------------------------------------------- utilidades */

const GREEN = '\x1b[32m';
const RED = '\x1b[31m';
const RESET = '\x1b[0m';

let ok = 0;
let failures = 0;
// Labels that came out red: the self-test shows them as proof that the
// mutation hurt where it had to hurt.
let reds = [];
// The self-test runs the gate once more per mutation: without this the output
// would be 6 x 51 lines of noise.
let verbose = true;
const passFn = (n) => { if (verbose) console.log(`  ${GREEN}OK${RESET}    ${n}`); ok++; };
const failFn = (n) => {
  if (verbose) console.log(`  ${RED}FAIL${RESET}  ${n}`);
  failures++;
  reds.push(n);
};
const check = (cond, nombre) => (cond ? passFn(nombre) : failFn(nombre));
// Scenario titles: the same switch as the checks, so the self-test can run
// the whole gate in silence.
const heading = (t) => { if (verbose) console.log(t); };

// Two setImmediate calls: the leerEstado() -> render() chain is chained
// microtasks; one loop turn drains them all, the second leaves the field clean
// for the next act.
const flush = () => new Promise((r) => setImmediate(() => setImmediate(r)));

/* ------------------------------------------------------------------ the DOM */

function element(id, writes) {
  const listeners = {};
  const classes = new Set();
  let text = '';
  const e = {
    id,
    style: {},
    innerHTML: '',
    disabled: false,
    hidden: true,
    paused: true,
    readyState: 0,
    currentTime: 0,
    error: null,
    crossOrigin: '',
    src: '',
    srcObject: null,
    playbackRate: 1,
    canPlayType() { return ''; },
    addEventListener(t, f) { (listeners[t] = listeners[t] || []).push(f); },
    removeEventListener(t, f) {
      if (listeners[t]) listeners[t] = listeners[t].filter((f2) => f2 !== f);
    },
    // The gate fires the events a browser would fire by itself.
    dispatch(t, ev) { (listeners[t] || []).slice().forEach((f) => f(ev || {})); },
    play() {
      // Every call counts: "one attempt in flight" is a number, not an
      // opinion (F-18).
      this._plays = (this._plays || 0) + 1;
      if (this._playError) {
        const e = new Error(this._playError.message || 'play() rejected');
        e.name = this._playError.name || 'Error';
        return Promise.reject(e);
      }
      this.paused = false;
      return Promise.resolve();
    },
    pause() { if (!this.paused) { this.paused = true; this.dispatch('pause'); } },
    _attrs: {},
    setAttribute(k, v) { this._attrs[k] = String(v); },
    getAttribute(k) { return this._attrs[k]; },
    appendChild() {},
    _clases: classes,
    classList: {
      add(c) { classes.add(c); },
      remove(c) { classes.delete(c); },
      contains(c) { return classes.has(c); },
    },
  };
  // Counted writes: without this "writes once" is an opinion.
  Object.defineProperty(e, 'textContent', {
    get() { return text; },
    set(v) { writes[id] = (writes[id] || 0) + 1; text = String(v); },
    configurable: true,
    enumerable: true,
  });
  return e;
}

/*
 * Made-up fetch. Returns what the player reads: {ok, status, json}.
 * `requests` records every /status-json.xsl — the per-tick counts come from
 * there. Any other endpoint (WHEP, playlist) stays outside the gate and
 * rejects: if the code ever asks for it, the scenario gives it away.
 *
 * Four knobs, as in reality:
 *   reachable  the network/tunnel is down (rejects, does not answer)
 *   statusCode      what the gate answers to a request with a credential
 *               (404 = dead link, 502 = broken tunnel)
 *   cookie      'muerta' = without the token, the cookie does NOT open: 404.
 *               With the token the request goes through anyway (it is the
 *               fallback). With a live cookie (default), a request without
 *               the token goes through too.
 *   cuelga      black hole: the request NEVER comes back. Only an abort from
 *               the waiter wakes it (F-09: unbounded, the reconnection chain
 *               freezes forever).
 */
function createFetch(fuente) {
  const requests = [];
  // Every call with its options: the gate can read whether the request
  // carried a signal (the bound) or drags one that does not belong to it.
  const calls = [];
  const f = function (url, opciones) {
    const u = String(url);
    calls.push({ url: u, opciones: opciones || {} });
    // The camera playlist, first: if there is none, the page does not start.
    if (u.indexOf('/cam/index.m3u8') !== -1) {
      requests.push(u);
      if (!fuente.reachable) return Promise.reject(new Error('network down'));
      if (fuente.cam404) {
        return Promise.resolve({
          ok: false, status: 404,
          text() { return Promise.resolve(''); },
        });
      }
      const age = fuente.camPdtAge != null ? fuente.camPdtAge : 0;
      const pdt = new Date(Date.now() - age).toISOString();
      const text = '#EXTM3U\n#EXT-X-VERSION:7\n#EXT-X-TARGETDURATION:1\n' +
        '#EXT-X-MEDIA-SEQUENCE:42\n#EXT-X-PROGRAM-DATE-TIME:' + pdt +
        '\n#EXTINF:1.0,\nseg041.m4s\n';
      return Promise.resolve({
        ok: true, status: 200,
        text() { return Promise.resolve(text); },
      });
    }
    if (u.indexOf('/music/') !== -1 && /\.json(\?|$)/.test(u)) {
      requests.push(u);
      if (!fuente.reachable) return Promise.reject(new Error('network down'));
      var body = null;
      if (u.indexOf('catalog.json') !== -1) body = fuente.catalogo;
      else body = fuente.sidecar;
      if (!body) {
        // debug
      }
      if (!body) {
        return Promise.resolve({ ok: false, status: 404, json() { return Promise.resolve({}); }, text() { return Promise.resolve(''); } });
      }
      return Promise.resolve({ ok: true, status: 200, json() { return Promise.resolve(body); }, text() { return Promise.resolve(JSON.stringify(body)); } });
    }
    if (u.indexOf('/status-json.xsl') === -1) {
      return Promise.reject(new Error('outside the gate: ' + u));
    }
    requests.push(u);
    if (!fuente.reachable) return Promise.reject(new Error('network down'));

    // Black hole: nobody answers. The only wake-up call is the waiter's own
    // abort; if the code does not cut it, the promise (and the reconnection
    // chain behind it) stays pending forever.
    if (fuente.cuelga) {
      return new Promise(function (_listo, fallo) {
        const s = opciones && opciones.signal;
        if (!s) return;
        s.addEventListener('abort', function () {
          const e = new Error('cut by the bound');
          e.name = 'AbortError';
          fallo(e);
        });
      });
    }

    const respond = (statusCode) => ({
      ok: statusCode === 200,
      status: statusCode,
      json() { return Promise.resolve(statusCode === 200 ? { icestats: { source: fuente.data } } : {}); },
    });

    // The gate/tunnel rules every request: that is what answers when it has
    // the last word (404 = no valid credential).
    if (fuente.statusCode && fuente.statusCode !== 200) return Promise.resolve(respond(fuente.statusCode));

    // Without the token on the request, only the cookie can open.
    if (!/[?&]t=/.test(u) && fuente.cookie === 'muerta') {
      return Promise.resolve(respond(404));
    }
    return Promise.resolve(respond(200));
  };
  f.requests = requests;
  f.calls = calls;
  return f;
}

/*
 * Loads web/index.html's <script> into an isolated context and returns knobs
 * to drive it: fire intervals, read the DOM, change what fetch answers.
 */
async function openPage(config) {
  const writes = {};
  const elements = {};
  const intervals = [];
  const timeouts = [];
  let nextId = 1;
  const fetchSource = Object.assign({ reachable: true, statusCode: 200, data: null }, config);

  const fetch = createFetch(fetchSource);
  const created = [];

  // The address WITH history: the page's replaceState is visible here.
  // Without this "the bar is clean" would be the gate's opinion, not a
  // measurement (F-01: the token really has to leave the bar).
  const location = { search: config.search || '', host: 'localhost:8090', hostname: 'localhost', origin: 'http://localhost:8090', pathname: '/' };
  const replacements = [];
  const history = {
    replaceState(_estado, _titulo, url) {
      const u = new URL(String(url), 'http://localhost:8090');
      location.search = u.search;
      location.pathname = u.pathname;
      replacements.push(String(url));
    },
  };

  const document = {
    title: 'comechingona',
    head: { appendChild() {} },
    createElement(tag) { const e = element(tag + '#nuevo', writes); created.push(e); return e; },
    getElementById(id) {
      // If the gate (or the code) asks for an id the page does not have, the
      // check runs against a phantom element and passes for nothing: that is
      // exactly how a gate falls over without noticing.
      if (!IDS.has(id)) throw new Error('id that does not exist in web/index.html: ' + id);
      if (!elements[id]) elements[id] = element(id, writes);
      return elements[id];
    },
    addEventListener() {},
  };

  const context = {
    document: document,
    window: {},
    location: location,
    history: history,
    fetch,
    URLSearchParams,
    console,
    setInterval(fn, ms) { intervals.push({ fn, ms }); return intervals.length; },
    clearInterval() {},
    // Ids are stable (and unique) because `correrTimeouts()` empties the list:
    // with positional ids a late clearTimeout would kill the wrong clock and
    // the gate would lie in green.
    setTimeout(fn, ms) {
      const t = { id: nextId++, fn, ms, cancelled: false };
      timeouts.push(t);
      return t.id;
    },
    clearTimeout(id) {
      const t = timeouts.filter((x) => x.id === id)[0];
      if (t) t.cancelled = true;
    },
    // The browser has it; the page's code uses it for the bound.
    AbortController,
  };
  vm.createContext(context);
  new vm.Script(CODE, { filename: 'web/index.html#<script>' }).runInContext(context);
  await flush();
  // Live knob, in scope: the page is already running and from here on play()
  // rejects with whatever error it is given.
  if (config.playRechaza) {
    document.getElementById('audio')._playError = { name: config.playRechaza, message: 'the browser blocked autoplay' };
  }

  const el = (id) => document.getElementById(id);
  return {
    writes,
    intervals,
    timeouts,
    fetch,
    location: location,
    replacements,
    // The object fetch reads: it can be changed live so the next tick sees
    // another reality (404, network down, 502...).
    config: fetchSource,
    document,
    el,
    created,
    // Fires the given interval (or all of them) and lets the promises fall.
    async disparar(ms) {
      const selected = ms == null ? intervals : intervals.filter((i) => i.ms === ms);
      selected.forEach((i) => i.fn());
      await flush();
      return selected.length;
    },
    async correrTimeouts() {
      const oldOnes = timeouts.splice(0, timeouts.length);
      const alive = oldOnes.filter((t) => !t.cancelled);
      alive.forEach((t) => t.fn());
      await flush();
      return alive.length;
    },
  };
}

/* --------------------------------------------------------------- escenarios */

// The player swallows the whole <script> in one go (inline or ordered external modules).
let CODE = '';
let HTML = '';
try {
  HTML = fs.readFileSync(PAGE, 'utf8');
  const m = HTML.match(/<script>([\s\S]*?)<\/script>/);
  if (m) {
    CODE = m[1];
  } else {
    const srcs = [];
    for (const sm of HTML.matchAll(/<script\s+src="([^"]+)"[^>]*><\/script>/g)) {
      srcs.push(sm[1]);
    }
    if (!srcs.length) throw new Error('no <script> block without attributes and no external scripts');
    CODE = srcs.map((s) => {
      const rel = s.replace(/^\//, '');
      return fs.readFileSync(path.join(ROOT, 'web', rel), 'utf8');
    }).join('\n');
  }
  for (const id of HTML.matchAll(/\sid="([^"]+)"/g)) IDS.add(id[1]);
} catch (e) {
  console.log(`\n  ${RED}FAIL${RESET}  could not read the script in web/index.html: ${e.message}\n`);
  process.exit(1);
}

// The air: the source Icecast reports when the radio is on air.
const AIR = {
  server_name: 'Radio Comechingona',
  title: 'Steady Hand — Radio Comechingona',
  listeners: 3,
};

// The gate's fetch now serves two endpoints: the status and the camera's
// probe. These helpers exist so the tick's assertions count ONLY the status
// (F-08: the probe must not count towards "exactly one fetch per tick").
const statusOnly = (p) => p.fetch.requests.filter(u => u.includes('/status-json.xsl'));

async function escenarioUnTick() {
  heading('\none status tick:');
  const p = await openPage({ data: AIR });

  // Start-up: one immediate check, not two.
  check(statusOnly(p).length === 1, 'on load: exactly 1 status fetch');
  check((p.writes.now || 0) === 1, 'on load: #now is written 1 time');
  check(p.el('estado').textContent === 'en vivo', 'on load: the banner says live');

  // No other interval in the player may talk to the endpoint.
  const whatFetched = [];
  for (const i of p.intervals) {
    const before = statusOnly(p).length;
    i.fn();
    await flush();
    if (statusOnly(p).length > before) whatFetched.push(i.ms);
  }
  check(
    whatFetched.length === 1 && whatFetched[0] === 15000,
    `only one interval polls the status (15000 ms); found: [${whatFetched.join(', ')}]`
  );

  // Each tick: +1 fetch, +1 #now write, +1 status write.
  const before = { ped: statusOnly(p).length, now: p.writes.now || 0, est: p.writes.estado || 0 };
  await p.disparar(15000);
  check(statusOnly(p).length - before.ped === 1, '15 s: the tick makes exactly 1 fetch');
  check((p.writes.now || 0) - before.now === 1, '15 s: #now is written 1 time');
  check((p.writes.estado || 0) - before.est === 1, '15 s: the banner is written 1 time');

  const now = { ped: statusOnly(p).length, now: p.writes.now || 0 };
  await p.disparar(15000);
  check(statusOnly(p).length - now.ped === 1, 'another tick: still 1 fetch, they do not pile up');
  check((p.writes.now || 0) - now.now === 1, 'another tick: #now, 1 time again');

  check(p.el('now').textContent.trim() === AIR.title, '#now shows the title, not server_name');
  check(p.document.title === `${AIR.title} — comechingona`, 'the document title follows the track');
}

async function escenarioCadenaManda() {
  heading('\nthe reconnection chain outranks the tick:');
  const p = await openPage({ data: AIR });
  check(p.el('estado').textContent === 'en vivo', 'starts live');

  // A cut: the same path the browser would use (audio error).
  p.el('audio').dispatch('error');
  await flush();
  check(p.el('estado').textContent === 'reconectando', 'the cut sets reconnecting');
  check(!p.el('dot').classList.contains('live'), 'the green dot goes off');
  check(p.timeouts.length > 0, 'the retry was scheduled');

  // The bug was here: the live tick painted "live" over "reconnecting".
  const escritas = p.writes.estado || 0;
  const before = statusOnly(p).length;
  await p.disparar(15000);
  check(statusOnly(p).length - before === 1, 'the tick keeps running (the page does not freeze)');
  check(p.el('estado').textContent === 'reconectando', 'tick live: the banner does NOT turn live');
  check((p.writes.estado || 0) === escritas, 'tick live: not one banner write');

  // And with the backend down neither: "Radio off air" does not rule either.
  p.config.reachable = false;
  await p.disparar(15000);
  check(p.el('estado').textContent === 'reconectando', 'tick with no backend: the banner stays intact');

  // The chain ends on its own and the tick rules again.
  await p.correrTimeouts();           // reintento agendado -> conectar()
  p.el('audio').dispatch('playing');   // el stream volvio
  check(p.el('estado').textContent === 'en vivo', 'stream back: live again');
  p.config.reachable = true;
  await p.disparar(15000);
  check(p.el('estado').textContent === 'en vivo', 'with the chain closed, the tick paints again');
}

async function escenarioDosFormas() {
  heading('\nthe endpoint\'s two shapes paint the same (F-15):');
  const objectVal = await openPage({ data: AIR });
  const arrayVal = await openPage({ data: [AIR] });
  const twoVal = await openPage({ data: [AIR, { server_name: 'segundo mount' }] });

  const readValues = (p) => [
    p.el('now').textContent,
    p.el('estado').textContent,
    p.el('aviso').style.display,
    p.document.title,
  ].join(' | ');

  const a = readValues(objectVal);
  const b = readValues(arrayVal);
  const c = readValues(twoVal);
  check(a === b, `object and arrayVal give the same render\n        object: ${a}\n        arrayVal:  ${b}`);
  check(b === c, 'with two mounts the first is read, same as with one');
  check(/Radio Comechingona/.test(a), 'the render carries title');
}

/* ---------------------------------------------- the door and the air */

// The door's sentence, exactly as it must appear on screen (without the <br>).
const DOOR_PHRASE = 'El servidor respondió 404.\n' +
  'El acceso expiró o el navegador no guardó la cookie. Pedí un link nuevo.';

const getErrorText = (p) => String(p.el('err').innerHTML || '').replace(/<br\s*\/?>/gi, '\n');

// "Painted nothing": which tick surfaces were touched (empty = none).
function paintFailures(p) {
  const f = [];
  if (p.writes.estado) f.push(`the status was written ${p.writes.estado} time(s)`);
  if (p.writes.now) f.push(`#now was written ${p.writes.now} time(s)`);
  if ('display' in p.el('err').style) f.push('the error banner was touched');
  if ('display' in p.el('aviso').style) f.push('the notice was touched');
  if (p.el('play').disabled) f.push('the button was left disabled');
  if (p.el('dot')._clases.size) f.push('the dot was touched: ' + [...p.el('dot')._clases].join(','));
  return f;
}
const intact = (p) => paintFailures(p).length === 0;
const whereFails = (p) => paintFailures(p).join(' / ');

async function escenarioMatriz() {
  heading('\nthe tick matrix at load (F-03 / F-04):');

  const aire = await openPage({ data: AIR });
  check(aire.el('estado').textContent === 'en vivo' && aire.el('play').disabled === false,
    '200 with source -> live, button enabled');
  check(aire.el('aviso').style.display === 'none' && aire.el('err').style.display !== 'block',
    '200 with source -> no notices');
  check(/Radio Comechingona/.test(aire.el('now').textContent), '200 with source -> #now with the track');

  const sinAire = await openPage({ data: null });
  check(sinAire.el('aviso').textContent === 'La transmisión está detenida'
        && sinAire.el('aviso').style.display === 'block',
    '200 without source -> "La transmisión está detenida"');
  check(sinAire.el('estado').textContent === 'detenido' && sinAire.el('play').disabled === true,
    '200 without source -> stopped and a dead button');

  // THIS is the row that did not exist: the closed door speaking when it
  // matters most — at load, without ever pressing play.
  const puerta = await openPage({ statusCode: 404 });
  check(getErrorText(puerta) === DOOR_PHRASE,
    `404 -> the door's exact sentence\n        ${getErrorText(puerta).replace(/\n/g, ' / ')}`);
  check(puerta.el('err').style.display === 'block', '404 -> the door\'s banner is visible');
  check(puerta.el('aviso').style.display !== 'block', '404 -> it does NOT say "Broadcast is stopped"');
  check(puerta.el('estado').textContent === 'error', '404 -> the status says error, not stopped');
  check(puerta.el('play').disabled === true, '404 -> dead button (there is nothing to play)');
  check(/Pedí un link nuevo/.test(getErrorText(puerta)),
    '404 -> "Pedí un link nuevo" is in sight at load');

  const rechazo = await openPage({ reachable: false });
  check(statusOnly(rechazo).length === 1, 'network down -> it still fetches (it measures, it does not claim)');
  check(intact(rechazo),
    `network down -> paints nothing: not off air, not the door, not a dead button${intact(rechazo) ? '' : `\n        ${whereFails(rechazo)}`}`);

  const error502 = await openPage({ statusCode: 502 });
  check(intact(error502),
    `502 from the tunnel -> it does not lie either${intact(error502) ? '' : `\n        ${whereFails(error502)}`}`);
}

async function escenarioTransitoria() {
  heading('\na transient failure does not turn the radio off (F-04):');
  const p = await openPage({ data: AIR });
  p.el('audio').dispatch('playing'); // the audio is really playing
  check(p.el('estado').textContent === 'en vivo' && p.el('play').disabled === false,
    'starts live, button enabled');

  p.config.reachable = false;
  const before = statusOnly(p).length;
  await p.disparar(15000);
  check(statusOnly(p).length - before === 1, 'the tick still polls: it measured, it did not go mute');
  check(p.el('estado').textContent === 'en vivo', 'the status stays live');
  check(p.el('play').disabled === false, 'the button stays enabled');
  check(p.el('aviso').style.display !== 'block', 'no notice appears');
  check(!/off air/i.test(p.el('aviso').textContent + p.el('err').innerHTML),
    'neither the notice nor the banner says "off air"');
  check(p.el('dot').classList.contains('live'), 'the green dot stays lit');

  p.config.reachable = true;
  await p.disparar(15000);
  check(p.el('estado').textContent === 'en vivo', 'network back -> the tick resumes painting on its own');
}

async function escenarioPuerta() {
  heading('\nthe door, through the two paths that discover it:');

  // Path 1: the chain. The token expires mid-session, the stream falls,
  // alFallar() asks the gate, and the gate says 404.
  const cadena = await openPage({ data: AIR });
  cadena.config.statusCode = 404;
  cadena.el('audio').dispatch('error');
  await flush();
  check(cadena.el('err').style.display === 'block', 'the chain reached falloDefinitivo');
  check(getErrorText(cadena) === DOOR_PHRASE, 'the chain writes the SAME sentence');

  // Path 2: the tick. There was never audio: just the page and its 404.
  const tick = await openPage({ statusCode: 404 });
  check(getErrorText(tick) === getErrorText(cadena), 'the two paths cannot diverge');

  // And they cannot diverge in the future: the sentence is written once.
  const veces = CODE.split('El acceso expiró o el navegador no guardó').length - 1;
  check(veces === 1, `the door's sentence is written only once in the code (${veces})`);
}

/* ------------------------------------------- the token in the bar (F-01) */

// Any string: the gate does not validate tokens, it validates where they travel.
const TOKEN_DE_PRUEBA = 'f00dfeed';

async function escenarioTokenFueraDeLaBarra() {
  heading('\nthe token leaves the address bar (F-01):');

  // 1) Normal browser: the cookie opens and the first request — WITHOUT the
  //    token — already proves it. That is the request that has to exist: asking
  //    with the token is not asking, it is spending it.
  const viva = await openPage({ data: AIR, search: '?t=' + TOKEN_DE_PRUEBA });
  check(viva.location.search === '',
    `live cookie: the bar is clean on load (left "${viva.location.search}")`);
  check(viva.replacements.length === 1,
    `live cookie: one replaceState, not zero or three (${viva.replacements.length})`);
  check(statusOnly(viva).length === 1,
    `live cookie: one single request — the one that tests the cookie (${statusOnly(viva).length})`);
  check(statusOnly(viva).every((u) => !/[?&]t=/.test(u)),
    'live cookie: NO request carries the token');
  check(viva.el('estado').textContent === 'en vivo',
    'live cookie: the radio starts live without spending the token');
  await viva.disparar(15000);
  check(statusOnly(viva).length === 2 && !/[?&]t=/.test(statusOnly(viva)[1]),
    'live cookie: the tick still goes without the token');

  // 2) Browser with no cookies (Safari ITP, etc.): the fallback has to stay
  //    alive — the t travels on the requests — and the bar is cleaned anyway.
  //    Principle 6 first: the token does not live in the address.
  const muerta = await openPage({ data: AIR, search: '?t=' + TOKEN_DE_PRUEBA, cookie: 'muerta' });
  check(muerta.location.search === '',
    `dead cookie: the bar is clean TOO (left "${muerta.location.search}")`);
  check(statusOnly(muerta).length === 2,
    `dead cookie: test without t (404) + verdict with t (${statusOnly(muerta).length})`);
  check(!/[?&]t=/.test(statusOnly(muerta)[0]),
    'dead cookie: the first request tests the cookie WITHOUT the token');
  check(/[?&]t=/.test(statusOnly(muerta)[1]),
    'dead cookie: the verdict comes with the token (the fallback)');
  check(muerta.el('estado').textContent === 'en vivo',
    'dead cookie: the fallback holds the radio up');
  await muerta.disparar(15000);
  check(/[?&]t=/.test(statusOnly(muerta)[2]),
    'dead cookie: the ticks keep carrying the token');
  check(muerta.el('estado').textContent === 'en vivo',
    'dead cookie: on the next tick it is still live (it did not fall to the 404)');

  // 3) Dead link: the door speaks, with the bar just as clean.
  const expirado = await openPage({
    data: AIR, search: '?t=' + TOKEN_DE_PRUEBA, cookie: 'muerta', statusCode: 404,
  });
  check(getErrorText(expirado) === DOOR_PHRASE, 'dead link: the door\'s sentence, not "off air"');
  check(expirado.location.search === '',
    'dead link: the bar is cleaned even though the link died');

  // 4) Could not ask: nothing is decided. Cleaning the bar here would be
  //    throwing the fallback into the void — and deciding "dead cookie" with
  //    the radio down would be lying.
  const caida = await openPage({ data: AIR, reachable: false, search: '?t=' + TOKEN_DE_PRUEBA });
  check(caida.location.search === '?t=' + TOKEN_DE_PRUEBA,
    'radio down: the bar is NOT cleaned — nothing proven, nothing thrown away');
  check(statusOnly(caida).length === 1, 'radio down: one attempt per tick');
  check(intact(caida), 'radio down: and it paints nothing');
  caida.config.reachable = true;
  await caida.disparar(15000);
  check(caida.location.search === '',
    'radio back: the tick that can answer cleans the bar');
  check(!/[?&]t=/.test(statusOnly(caida)[statusOnly(caida).length - 1]),
    'radio back: and the token no longer travels');
  check(caida.el('estado').textContent === 'en vivo',
    'radio back: the verdict comes from the probe, not from a phantom');

  // 5) Broken tunnel (502): a proxy error proves nothing either.
  const tunel = await openPage({ statusCode: 502, search: '?t=' + TOKEN_DE_PRUEBA });
  check(tunel.location.search === '?t=' + TOKEN_DE_PRUEBA,
    '502: the bar stays as it was — 502 proves nothing');
  check(statusOnly(tunel).length === 1,
    '502: one single attempt (the question repeats, it is not answered)');
  check(intact(tunel), '502: and it paints nothing');

  // 6) ?debug=1 survives the token: the t is removed, not the knob.
  const dbg = await openPage({ data: AIR, search: '?t=' + TOKEN_DE_PRUEBA + '&debug=1' });
  check(dbg.location.search === '?debug=1',
    `debug: the t is removed and debug stays (left "${dbg.location.search}")`);
  check(dbg.intervals.some((i) => i.ms === 1000), 'debug: the panel stays on');
}

/* ---------------------------------------- the station's banner (F-10) */

// The autoplay sentence, exactly as it must appear on screen.
const FRASE_AUTOPLAY = 'El navegador bloqueó la reproducción automática. Presioná reproducir.';

async function escenarioVozDeLaEstacion() {
  heading('\nthe station\'s banner, in one voice (F-10 / F-11):');

  check(CODE.includes('var STR = {'), 'the copy lives in ONE object (STR)');
  // No visible content written by hand: every write to textContent/innerHTML
  // references STR, variables or functions. An assignment with a literal
  // sentence outside STR breaks here.
  const mano = CODE.match(/\.(textContent|innerHTML|title)\s*=\s*'[^'\n]*[A-Za-zÀ-ÿ][^'\n]*'/g) || [];
  check(mano.length === 0,
    `not one visible sentence hand-written outside STR${mano.length ? `\n        e.g. "${mano[0]}"` : ''}`);
  check(/<html lang="es">/.test(HTML), 'the copy is in Spanish and <html> says so');

  for (const id of ['estado', 'err', 'aviso']) {
    const tag = HTML.match(new RegExp('<[^>]+id="' + id + '"[^>]*>'));
    check(!!tag && /aria-live="polite"/.test(tag[0]), `aria-live="polite" on #${id}`);
  }

  // The button's label travels with the state: Play when stopped, Pause when playing.
  const p = await openPage({ data: AIR });
  check(p.el('play')._attrs['aria-label'] === undefined,
    'on load: the label comes from the static HTML');
  p.el('audio').dispatch('playing');
  check(p.el('play')._attrs['aria-label'] === 'Pausar', 'when playing: the button says Pause');
  p.el('audio').dispatch('pause');
  check(p.el('play')._attrs['aria-label'] === 'Reproducir', 'when stopped: the button says Play');
}

/* -------------------------------------------- the browser's policy */

async function escenarioAutoplayBloqueado() {
  heading('\nblocked autoplay says what it is (F-16):');

  const p = await openPage({ data: AIR, playRechaza: 'NotAllowedError' });
  p.el('audio').dispatch('playing'); // the audio was really playing
  check(p.el('estado').textContent === 'en vivo', 'starts live, sounding');

  p.el('audio').dispatch('error');   // the stream cuts
  await flush();                     // alFallar -> fetch -> reintentar (scheduled)
  check(p.el('estado').textContent === 'reconectando', 'the cut sets reconnecting');
  check(p.timeouts.length > 0, 'the retry was scheduled');

  await p.correrTimeouts();          // the retry runs -> conectar(true) -> play() rejects
  check(p.el('aviso').style.display === 'block' && p.el('aviso').textContent === FRASE_AUTOPLAY,
    'play() rejected with NotAllowedError -> the dedicated copy, NOT silence');
  check(p.el('estado').textContent === 'bloqueado',
    'the status says blocked, NOT reconnecting or stopped');
  check(p.el('play').disabled === false, 'the button stays alive: the click resolves it');
  check(!p.el('dot').classList.contains('live'), 'the green dot goes off');

  // The tick does not paint over the block: the air does not unblock a browser.
  const pintaAntes = p.writes.estado || 0;
  await p.disparar(15000);
  check(p.el('estado').textContent === 'bloqueado', 'the tick does NOT paint over the block: the banner stays');
  check((p.writes.estado || 0) === pintaAntes, 'the tick measures but does not write the banner');
  check(p.el('aviso').style.display === 'block', 'and the notice still stands');

  // The click comes with a user gesture: the browser lets it play.
  p.el('audio')._playError = null;
  p.el('play').dispatch('click');
  p.el('audio').dispatch('playing');
  check(p.el('estado').textContent === 'en vivo', 'the click resolves what autoplay could not');
  check(p.el('play')._attrs['aria-label'] === 'Pausar', 'and the button says Pause');
}

/* ------------------------------- the listener's hand (F-17 / F-18) */

const MAX_R = Number((CODE.match(/var MAX_REINTENTOS = (\d+);/) || [])[1] || 0);

async function escenarioManoDelOyente() {
  heading('\nthe listener\'s hand outranks the chain (F-17 / F-18):');

  check(MAX_R > 0, `the chain's budget is read from the code (${MAX_R})`);

  // F-17: after the attempts are spent, a manual retry starts with the whole
  // budget — the retry's first failure goes back to (1/5), not to the
  // definitive banner.
  const p = await openPage({ data: AIR });
  p.el('audio').dispatch('playing');
  for (let i = 0; i <= MAX_R; i++) {   // MAX_R failures + the one that spends it
    p.el('audio').dispatch('error');
    await flush();
    await p.correrTimeouts();
  }
  check(/No se pudo conectar/.test(String(p.el('err').innerHTML || '')),
    'budget spent: definitive banner');
  check(pendientes(p).length === 0, 'and nothing is left scheduled');

  p.el('play').dispatch('click');       // the listener retries by hand
  p.el('audio').dispatch('error');      // and fails again
  await flush();
  check(String(p.el('err').textContent).indexOf('(1/' + MAX_R + ')') !== -1,
    `the manual retry starts at (1/${MAX_R}), it does not hit the exhausted counter`);

  // F-18: a click during the wait cancels what is scheduled — one attempt in
  // flight, always.
  const q = await openPage({ data: AIR });
  q.el('audio').dispatch('playing');
  q.el('audio').dispatch('error');
  await flush();
  check(pendientes(q).filter((t) => t.ms === 800).length === 1,
    'the first attempt was scheduled (800 ms)');
  const before = q.el('audio')._plays || 0;
  q.el('play').dispatch('click');
  check((q.el('audio')._plays || 0) - before === 1, 'the click starts ONE attempt, now');
  check(pendientes(q).filter((t) => t.ms === 800).length === 0,
    'and the scheduled one was cancelled');
  await q.correrTimeouts();
  check((q.el('audio')._plays || 0) - before === 1,
    'running the clock afterwards does not start a second attempt in parallel');

  // F-18: the radio turned off by hand is not revived on its own. A paused
  // element can also emit `error`; the chain does not listen to it.
  const r = await openPage({ data: AIR });
  r.el('audio').dispatch('playing');
  r.el('audio').readyState = 4;        // really playing
  r.el('audio').paused = false;
  r.el('play').dispatch('click');       // the listener presses Pause
  check(r.el('estado').textContent === 'detenido',
    'manual pause: the banner says stopped (it does not get stuck on reconnecting)');
  const playsR = r.el('audio')._plays || 0;
  r.el('audio').dispatch('error');      // a later error from the paused element
  await flush();
  check(r.el('estado').textContent === 'detenido', 'the chain does not revive it');
  check((r.el('audio')._plays || 0) === playsR, 'not one attempt started');
  check(pendientes(r).length === 0, 'not one retry scheduled');
  await r.correrTimeouts();
  check((r.el('audio')._plays || 0) === playsR, 'and running the clock does not either: the audio does not come back on its own');

  // And the Play click turns it back on: off is not a padlock.
  r.el('play').dispatch('click');
  check((r.el('audio')._plays || 0) - playsR === 1, 'Play turns it on again');
}

/* ----------------------------------- the bounded poll (F-09 / S10) */

// The bound lives in the code, written once: the gate reads it from there
// instead of repeating the number on its own (two copies of the same number is
// how one ends up measuring the wrong place).
const BOUND_MS = Number((CODE.match(/var PROBE_MS = (\d+);/) || [])[1] || 0);

const pendientes = (p) => p.timeouts.filter((t) => !t.cancelled);

async function escenarioProbeAcotado() {
  heading('\nthe credential poll is bounded (F-09):');

  check(BOUND_MS > 0, `the bound exists and is a number (${BOUND_MS} ms)`);
  const declaraciones = CODE.split('var PROBE_MS =').length - 1;
  check(declaraciones === 1, `the bound is declared only once (${declaraciones})`);

  const p = await openPage({ data: AIR });
  p.el('audio').dispatch('playing');
  check(p.el('estado').textContent === 'en vivo', 'starts live, sounding');

  // The cut, and the gate swallows the request: black hole, no RST.
  p.config.cuelga = true;
  const before = statusOnly(p).length;
  p.el('audio').dispatch('error');
  await flush();
  check(statusOnly(p).length - before === 1, 'the cut asks for the credential (1 request)');

  const probe = p.fetch.calls.filter((c) => c.url.indexOf('/status-json.xsl') !== -1).pop();
  check(!!(probe.opciones && probe.opciones.signal), 'the request carries a signal: it can be cut');
  check(probe.opciones.cache === 'no-store' && probe.opciones.credentials === 'include',
    'and asks with the same rules as the tick (cache/credentials)');
  const cota = pendientes(p).filter((t) => t.ms === BOUND_MS);
  check(cota.length === 1, `the ${BOUND_MS} ms bound was armed (${cota.length})`);

  // Without the bound, it stays here: the `reintentando` guard swallows the
  // tick, the `ended` and the `error`, there is no banner and the clock does
  // not run. The page looks alive: it is exactly the failure F-09 describes.
  const pintadas = p.writes.estado || 0;
  await p.disparar(15000);
  check(p.el('estado').textContent === 'en vivo', 'hung: the banner does not change (the cut was not announced)');
  check((p.writes.estado || 0) === pintadas, 'hung: the tick measures but does not paint');
  check(p.el('err').style.display !== 'block', 'hung: not one error banner');

  // And then the bound fires: the abort reads as network, the chain advances.
  await p.correrTimeouts();   // abort -> catch -> reintentar -> attempt 1 scheduled
  check(p.el('estado').textContent === 'reconectando', 'the bound fires: the chain advances on its own');
  check(String(p.el('err').textContent).indexOf('(1/5)') !== -1,
    'it reached attempt 1 (1/5)');
  check(!/Pedí un link nuevo/.test(String(p.el('err').innerHTML || '')),
    'the abort is NOT read as the door: it proves nothing about the credential');
  check(pendientes(p).filter((t) => t.ms === 800).length === 1,
    'attempt 1 was scheduled (800 ms)');

  // The chain does not get stuck: with the network back, the attempt runs and
  // the stream returns.
  p.config.cuelga = false;
  await p.correrTimeouts();   // intento 1: conectar(true)
  p.el('audio').dispatch('playing');
  check(p.el('estado').textContent === 'en vivo', 'stream back: the chain closes on live');

  // And the bound is not pasted onto the shared object: the next tick asks
  // without a signal (an aborted signal there would kill every tick forever).
  const q = await openPage({ data: AIR });
  q.el('audio').dispatch('error');
  await flush();
  await q.disparar(15000);
  const tickQ = q.fetch.calls.filter((c) => c.url.indexOf('/status-json.xsl') !== -1).pop();
  check(!(tickQ.opciones && tickQ.opciones.signal),
    'the bound is not pasted onto the shared object: the tick asks without a signal');
}


/* ------------------------------------- the seed on stage (S11 / D-1) */

async function escenarioSemilla() {
  heading('\\nthe seed on stage (S11):');

  // Absent: no manifest, no sidecar -> the line stays hidden, never faked.
  const sin = await openPage({ data: AIR });
  sin.el('audio').dispatch('playing');
  await flush();
  await sin.disparar(15000);
  check(sin.el('seed').hidden === true, 'no manifest: the seed line stays hidden');

  // Present: catalog + sidecar match the playing track -> lane · seed · plan_sha256.
  const hash = '1ce57e51abcdef';
  const cfgCon = {
    data: AIR,
    catalogo: { entries: [{ path: 'motorik_steady_hand_s100.flac', lane: 'motorik', seed: 100 }] },
    sidecar: { lane: 'motorik', seed: 100, plan_sha256: hash },
  };
  const con = await openPage(cfgCon);
  con.el('audio').dispatch('playing');
  await flush();
  await con.disparar(15000);
  check(con.el('seed').hidden === false, 'manifest present: the seed line appears');
  check(String(con.el('seed').textContent).indexOf('motorik') !== -1
     && String(con.el('seed').textContent).indexOf('100') !== -1
     && String(con.el('seed').textContent).indexOf(hash) !== -1,
    'the line reads lane · seed · plan_sha256');
}

/* ----------------------------- journey: cut -> reconnect (S13 / F-19) */

async function escenarioViajeCorteReconexion() {
  heading('\\njourney: a cut reconnects on its own (S13):');

  const p = await openPage({ data: AIR });
  p.el('audio').dispatch('playing');
  await flush();
  check(p.el('estado').textContent === 'en vivo', 'starts live');

  // The cut.
  p.el('audio').dispatch('error');
  await flush();
  check(p.el('estado').textContent === 'reconectando', 'the cut opens the reconnection chain');

  // The probe answers 200: not the door, retrying is worth it.
  check(p.el('err').style.display === 'block', 'the banner announces the retry');

  // Run the scheduled attempt: the audio reconnects.
  await p.correrTimeouts();   // attempt 1 -> conectar(true)
  p.el('audio').dispatch('playing');
  await flush();
  check(p.el('estado').textContent === 'en vivo', 'the stream comes back: live again');
  check(p.el('err').style.display !== 'block', 'and the banner clears');
}

/* ----------------------------- journey: expiry at load (S13 / F-19) */

async function escenarioViajeExpiracion() {
  heading('\\njourney: an expired link shows the door (S13):');

  // Dead cookie at load: the probe answers 404 -> door copy, and "ask for a
  // new link" is reachable (F-03).
  const p = await openPage({ statusCode: 404, search: '?t=' + TOKEN_DE_PRUEBA, cookie: 'muerta' });
  await flush();
  await p.disparar(15000);
  await flush();
  check(/Pedí un link nuevo/.test(String(p.el('err').innerHTML || '')),
    'expired link: the door copy is shown');
  check(p.el('estado').textContent === 'error', 'the status reads error (door closed)');
}

/* ----------------------------- journey: off-air at runtime (S13 / F-19) */

async function escenarioViajeFueraDeAire() {
  heading('\\njourney: the radio goes off-air (S13):');

  const p = await openPage({ data: AIR });
  p.el('audio').dispatch('playing');
  await flush();
  await p.disparar(15000);
  await flush();
  check(p.el('estado').textContent === 'en vivo', 'starts live');

  // The source disappears: next tick answers 200 with no source.
  p.config.data = null;
  const before = p.writes.estado || 0;
  await p.disparar(15000);
  await flush();
  check(p.el('estado').textContent === 'detenido', 'off-air: the banner says stopped');
  check(p.el('aviso').style.display === 'block', 'and the notice appears');
  check(p.el('play').disabled === true, 'the button is disabled while off-air');
}

/* ----------------------------- journey: silence (no source) (S13 / F-19) */

async function escenarioViajeSilencio() {
  heading('\\njourney: silence is a valid broadcast (S13):');

  // Load with no source: the radio does not pretend to be live.
  const p = await openPage({ data: null });
  await flush();
  await p.disparar(15000);
  await flush();
  check(p.el('estado').textContent === 'detenido', 'silence: the banner says stopped');
  check(p.el('aviso').style.display === 'block', 'and the notice appears');
  check(p.el('play').disabled === true, 'the button is disabled');
  check(String(p.el('aviso').textContent).length > 0, 'the notice carries copy (not empty)');
}

/* ----------------------------- journey: the chain outranks the tick (S13) */

async function escenarioViajeCadenaManda() {
  heading('\\njourney: the chain outranks the tick (S13):');

  const p = await openPage({ data: AIR });
  p.el('audio').dispatch('playing');
  await flush();
  await p.disparar(15000);
  await flush();
  check(p.el('estado').textContent === 'en vivo', 'starts live');

  // The cut opens the chain.
  p.el('audio').dispatch('error');
  await flush();
  check(p.el('estado').textContent === 'reconectando', 'reconectando');

  // A tick fires while reconnecting: it must NOT paint 'en vivo' over it.
  const before = p.writes.estado || 0;
  await p.disparar(15000);
  await flush();
  check(p.el('estado').textContent === 'reconectando', 'the tick does not overwrite reconnecting with live');
  check((p.writes.estado || 0) === before, 'and does not even repaint');
}

/* ----------------------------- S12 (D-4/D-5/D-6): circle, caption, toast */

async function escenarioS12Cadenas() {
  heading('\\nS12: circle, camera caption, door toast present (D-4/D-5/D-6):');

  // The strings live in STR (one voice): no hand-written copy outside it.
  check(/cameraCaption:\s*'[^']+'/.test(CODE),
    'STR.cameraCaption is defined (camera caption, D-5)');
  check(/doorToast:\s*'[^']+'/.test(CODE),
    'STR.doorToast is defined (door toast, D-6)');

  // The elements exist in the page (circle = the live dot, already a circle).
  const p = await openPage({ data: AIR });
  check(!!p.el('cam-caption'), 'the camera caption element exists (D-5)');
  check(!!p.el('toast'), 'the door toast element exists (D-6)');
  check(!!p.el('dot'), 'the live dot (circle) exists (D-4)');
}

/* --------------------------------------------- the camera, for now (F-08) */

async function escenarioCamara() {
  heading('\nthe camera, the question before start-up (F-08):');

  // 404: no camera — the box does not even appear and NOBODY loads vendorJS.
  const c404 = await openPage({ data: AIR, cam404: true });
  check(c404.el('cam-caja').hidden === true, 'playlist 404 -> the box does NOT appear');
  const vendor404 = c404.created.filter(e => e.src && e.src.includes('hls.light.min.js'));
  check(vendor404.length === 0, 'playlist 404 -> zero hls.js loads');
  check(!c404.fetch.requests.some(u => u.indexOf('whep') !== -1), 'playlist 404 -> zero SDP to WHEP');

  // Fresh: PDT from 2s ago — the box appears.
  const fresca = await openPage({ data: AIR, camPdtAge: 2000 });
  fresca.el('cam').canPlayType = function () { return 'probably'; };
  await flush();                     // let hayCamara() -> porHls() run
  const vendorFresh = fresca.created.filter(e => e.src && e.src.includes('hls.light.min.js'));
  if (vendorFresh.length) {
    vendorFresh[0].onload();        // simulate the loaded script
    await flush();
  }
  // nativo(): cam.src assigned + loadedmetadata registered -> fire
  fresca.el('cam').dispatch('loadedmetadata');
  check(fresca.el('cam-caja').hidden === false, 'fresh playlist -> the box appears');

  // Stale: PDT from a minute ago — the box does NOT appear.
  const vieja = await openPage({ data: AIR, camPdtAge: 60000 });
  check(vieja.el('cam-caja').hidden === true, 'stale playlist (>10 s) -> the box does NOT appear');
  const vendorOld = vieja.created.filter(e => e.src && e.src.includes('hls.light.min.js'));
  check(vendorOld.length === 0, 'stale playlist -> zero hls.js loads');
}

/* ------------------------------------------------------------------ corrida */

async function correrEscenarios() {
  ok = 0; failures = 0; reds = [];
  await escenarioUnTick();
  await escenarioCadenaManda();
  await escenarioManoDelOyente();
  await escenarioDosFormas();
  await escenarioMatriz();
  await escenarioTransitoria();
  await escenarioPuerta();
  await escenarioTokenFueraDeLaBarra();
  await escenarioVozDeLaEstacion();
  await escenarioAutoplayBloqueado();
  await escenarioProbeAcotado();
  await escenarioSemilla();
  await escenarioViajeCorteReconexion();
  await escenarioViajeExpiracion();
  await escenarioViajeFueraDeAire();
  await escenarioViajeSilencio();
  await escenarioViajeCadenaManda();
  await escenarioS12Cadenas();
  await escenarioCamara();
  return { ok, failures, reds: reds.slice() };
}

/* ------------------------------------------------------------- self-test */

/*
  One mutation per guarantee the gate claims to guard. Each one breaks exactly
  one rule written in web/index.html and nothing else. The break happens on the
  string in memory: the page's file is not touched, so there is nothing to
  restore and no chance of leaving the radio mutated in the repository.

  See issue #23: a gate that was never seen failing is not verified, it is
  believed.
*/
const MUTACIONES = [
  {
    que: 'a second 15 s tick (the one fetch per tick rule)',
    aplica: (h) => h.replace('setInterval(tick, TICK_MS);',
      'setInterval(tick, TICK_MS); setInterval(tick, TICK_MS);'),
  },
  {
    que: 'the chain stops outranking the tick',
    aplica: (h) => h.replace(/if \(reintentando\) return;\n\n    \/\/ Rule 1b/,
      '// (guard mutated)\n\n    // Rule 1b'),
  },
  {
    que: 'the 404 is read like any other answer',
    aplica: (h) => h.replace("if (r.status === 404) return { veredicto: 'puerta' };",
      "if (r.status === 401) return { veredicto: 'puerta' };"),
  },
  {
    que: 'no data -> it paints again (the transient failure lie)',
    aplica: (h) => h.replace("if (f.veredicto === 'sin-data') return;",
      "if (f.veredicto === 'nunca') return;"),
  },
  {
    que: 'the endpoint\'s two shapes are read differently',
    aplica: (h) => h.replace('if (Array.isArray(s)) s = s[0] || null;', ''),
  },
  {
    que: 'the door\'s sentence is written twice',
    aplica: (h) => h.replace('  var STR = {',
      "  var PUERTA_COPIA = 'El acceso expiró o el navegador no guardó la cookie.';\n  var STR = {"),
  },
  {
    que: 'the bar is never cleaned (the t stays in the address)',
    aplica: (h) => h.replace("    q.delete('t');\n", ''),
  },
  {
    que: 'the suffix is dropped even when the cookie is dead',
    aplica: (h) => h.replace('          cookieProbada = true;\n          limpiarBarra();',
      "          cookieProbada = true;\n          sufijo = '';\n          limpiarBarra();"),
  },
  {
    que: 'the cookie test arrives with the token (it never asks)',
    aplica: (h) => h.replace("return fetch(BACKEND_URL + '/status-json.xsl', OPCIONES_ESTADO)",
      'return fetch(conT, OPCIONES_ESTADO)'),
  },
  {
    que: 'a 502 is read as 404: it decides even though nothing is known',
    aplica: (h) => h.replace('        if (r.status === 404) {',
      '        if (r.status === 404 || r.status === 502) {'),
  },
  {
    que: 'blocked autoplay is swallowed in silence (F-16)',
    aplica: (h) => h.replace("if (e && e.name === 'NotAllowedError') autoplayBloqueado();",
      '// autoplay swallowed'),
  },
  {
    que: 'a visible sentence is left hand-written, outside STR (F-10)',
    aplica: (h) => h.replace("    estado.textContent = STR.live;",
      "    estado.textContent = 'en vivo';"),
  },
  {
    que: 'the camera is shown even when the playlist is stale (F-08)',
    aplica: (h) => h.replace('        return (Date.now() - t) < 10000;',
      '        return true; // mutated: always fresh'),
  },
  {
    que: 'the credential poll becomes unbounded again (F-09: it hangs forever)',
    aplica: (h) => h.replace('    var reloj = corte ? setTimeout(function () { corte.abort(); }, PROBE_MS) : null;',
      '    var reloj = null; // mutated: unbounded'),
  },
  {
    que: 'the abort is read as the door (a non-answer "proves" the credential)',
    aplica: (h) => h.replace('      .catch(function () { sinCredencial = false; })',
      '      .catch(function () { sinCredencial = true; })'),
  },
  {
    que: 'the bound is declared twice (two numbers that believe they are the same)',
    aplica: (h) => h.replace('  var PROBE_MS = 3000;', '  var PROBE_MS = 3000;\n  var PROBE_MS = 3000;'),
  },
  {
    que: 'the bound\'s signal is pasted onto the shared options object',
    aplica: (h) => h.replace("    var opciones = corte\n      ? Object.assign({}, OPCIONES_ESTADO, { signal: corte.signal })\n      : OPCIONES_ESTADO;",
      '    var opciones = OPCIONES_ESTADO;\n    if (corte) opciones.signal = corte.signal;'),
  },
  {
    que: 'the click does not cancel the scheduled retry (the radio comes back alone)',
    aplica: (h) => h.replace("    cancelarReintento();\n    reintentando = false;\n    reintentos = 0;\n    if (sonando()) {",
      '    reintentando = false;\n    reintentos = 0;\n    if (sonando()) {'),
  },
  {
    que: 'the click does not renew the budget (the comment lies again, F-17)',
    aplica: (h) => h.replace("    cancelarReintento();\n    reintentando = false;\n    reintentos = 0;\n    if (sonando()) {",
      '    cancelarReintento();\n    reintentando = false;\n    if (sonando()) {'),
  },
  {
    que: 'the chain revives the radio the listener turned off by hand (F-18)',
    aplica: (h) => h.replace('    if (pausadoAMano) return;\n    reintentando = true;',
      '    reintentando = true;'),
  },
];

const sinAnsi = (s) => String(s).replace(/\x1b\[[0-9;]*m/g, '');
const primeraLinea = (s) => sinAnsi(s).split('\n')[0].trim();

async function selfTest() {
  const original = CODE;
  // Runs the gate silently and always restores the code to the real page.
  const correrCallado = async () => {
    verbose = false;
    try { return await correrEscenarios(); }
    finally { verbose = true; CODE = original; }
  };

  heading('\nSelf-test — can this gate fail?');
  console.log('');

  let problemas = 0;

  const real = await correrCallado();
  if (real.failures === 0) {
    console.log(`  ${GREEN}OK${RESET}    real page .......................... ${real.ok}/${real.ok} green`);
  } else {
    console.log(`  ${RED}FAIL${RESET}  the real page has ${real.failures} reds: fix them before believing the self-test`);
    problemas++;
  }

  for (const m of MUTACIONES) {
    const mutado = m.aplica(original);
    if (mutado === original) {
      // The code changed shape and the mutation no longer knows where to
      // cut: that is NOT a pass, it is a blind gate.
      console.log(`  ${RED}FAIL${RESET}  the mutation did not apply: ${m.que}`);
      problemas++;
      continue;
    }
    CODE = mutado;
    const r = await correrCallado();
    if (r.failures === 0) {
      console.log(`  ${RED}FAIL${RESET}  it stayed green with: ${m.que}`);
      problemas++;
    } else {
      console.log(`  ${GREEN}OK${RESET}    ${m.que}`);
      console.log(`          ${r.failures} reds, e.g. "${primeraLinea(r.reds[0])}"`);
    }
  }

  console.log('');
  if (problemas === 0) {
    console.log(`${GREEN}All good${RESET} - ${MUTACIONES.length} mutations caught.\n`);
    process.exit(0);
  }
  console.log(`${RED}${problemas} problem(s) in the self-test${RESET}\n`);
  process.exit(1);
}

(async () => {
  if (process.argv.includes('--self-test')) { await selfTest(); return; }

  heading('\nUI gate — web/index.html');
  const r = await correrEscenarios();

  console.log('');
  if (r.failures === 0) {
    console.log(`${GREEN}All good${RESET} - ${r.ok} checks.\n`);
    process.exit(0);
  }
  console.log(`${RED}${r.failures} of ${r.ok + r.failures} checks fail${RESET}\n`);
  process.exit(1);
})().catch((e) => {
  console.log(`\n  ${RED}FAIL${RESET}  the gate broke on its own: ${e && e.stack ? e.stack : e}\n`);
  process.exit(1);
});
