#!/usr/bin/env node
/*
 * ui-gate.js — mide la estructura del estado del reproductor.
 *
 *   ./scripts/ui-gate.js
 *
 * web/index.html no tiene build ni test runner: esto extrae su <script>, lo
 * corre contra un DOM y un fetch inventados, y cuenta lo que a ojo no se ve:
 * cuántos fetch de estado hay por tick, cuántas veces se escribe #now y qué
 * hace el cartel mientras hay una cadena de reconexión en vuelo.
 *
 * No toca la red, no toca la cámara y no necesita stack levantado.
 *
 * Codigos de salida:
 *   0  todas las escenarios pasan
 *   1  hay al menos una falla
 *
 * Slice S1 (un solo tick de estado). Tambien cubre la puerta de F-15:
 * las dos formas del endpoint pintan igual.
 */
'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const RAIZ = path.join(__dirname, '..');
const PAGINA = path.join(RAIZ, 'web', 'index.html');

/* --------------------------------------------------------------- utilidades */

const VERDE = '\x1b[32m';
const ROJO = '\x1b[31m';
const FIN = '\x1b[0m';

let ok = 0;
let mal = 0;
const pasa = (n) => { console.log(`  ${VERDE}OK${FIN}    ${n}`); ok++; };
const falla = (n) => { console.log(`  ${ROJO}FALLA${FIN}  ${n}`); mal++; };
const check = (cond, nombre) => (cond ? pasa(nombre) : falla(nombre));

// Dos setImmediate: la cadena leerEstado() -> render() son microtareas
// encadenadas; una vuelta del loop las drena todas, la segunda deja el
// campo limpio para el siguiente acto.
const flush = () => new Promise((r) => setImmediate(() => setImmediate(r)));

/* ------------------------------------------------------------------ el DOM */

function elemento(id, escrituras) {
  const oyendo = {};
  const clases = new Set();
  let texto = '';
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
    addEventListener(t, f) { (oyendo[t] = oyendo[t] || []).push(f); },
    removeEventListener(t, f) {
      if (oyendo[t]) oyendo[t] = oyendo[t].filter((f2) => f2 !== f);
    },
    // El gate dispara los eventos que un navegador dispararía solo.
    dispara(t, ev) { (oyendo[t] || []).slice().forEach((f) => f(ev || {})); },
    play() { this.paused = false; return Promise.resolve(); },
    pause() { if (!this.paused) { this.paused = true; this.dispara('pause'); } },
    appendChild() {},
    classList: {
      add(c) { clases.add(c); },
      remove(c) { clases.delete(c); },
      contains(c) { return clases.has(c); },
    },
  };
  // Escrituras contadas: sin esto "escribe una vez" es una opinion.
  Object.defineProperty(e, 'textContent', {
    get() { return texto; },
    set(v) { escrituras[id] = (escrituras[id] || 0) + 1; texto = String(v); },
    configurable: true,
    enumerable: true,
  });
  return e;
}

/*
 * fetch inventado. Devuelve lo que el reproductor lee: {ok, status, json}.
 * `pedidos` registra cada /status-json.xsl — de ahi salen los conteos por
 * tick. Cualquier otro endpoint (WHEP, playlist) queda fuera del gate y
 * rechaza: si el codigo llega a pedirlo, la escenario lo delata.
 */
function crearFetch(fuente) {
  const pedidos = [];
  const f = function (url) {
    const u = String(url);
    if (u.indexOf('/status-json.xsl') === -1) {
      return Promise.reject(new Error('fuera del gate: ' + u));
    }
    pedidos.push(u);
    if (!fuente.alcanzable) return Promise.reject(new Error('red caida'));
    if (fuente.codigo && fuente.codigo !== 200) {
      return Promise.resolve({
        ok: false,
        status: fuente.codigo,
        json() { return Promise.resolve({}); },
      });
    }
    return Promise.resolve({
      ok: true,
      status: 200,
      json() { return Promise.resolve({ icestats: { source: fuente.datos } }); },
    });
  };
  f.pedidos = pedidos;
  return f;
}

/*
 * Carga el <script> de web/index.html en un contexto de aislamiento y
 * devuelve perillas para manejarlo: disparar intervalos, leer el DOM,
 * cambiar lo que el fetch responde.
 */
async function abrirPagina(config) {
  const escrituras = {};
  const elementos = {};
  const intervalos = [];
  const timeouts = [];
  const fetchFuente = Object.assign({ alcanzable: true, codigo: 200, datos: null }, config);
  const fetch = crearFetch(fetchFuente);

  const doc = {
    title: 'Radio',
    head: { appendChild() {} },
    createElement(tag) { return elemento(tag + '#nuevo', escrituras); },
    getElementById(id) {
      if (!elementos[id]) elementos[id] = elemento(id, escrituras);
      return elementos[id];
    },
    addEventListener() {},
  };

  const ctx = {
    document: doc,
    window: {},
    location: { search: '', host: 'radio.daza.ar', pathname: '/' },
    fetch,
    URLSearchParams,
    console,
    setInterval(fn, ms) { intervalos.push({ fn, ms }); return intervalos.length; },
    clearInterval() {},
    setTimeout(fn, ms) { timeouts.push({ fn, ms }); return timeouts.length; },
    clearTimeout() {},
  };
  vm.createContext(ctx);
  new vm.Script(CODIGO, { filename: 'web/index.html#<script>' }).runInContext(ctx);
  await flush();

  const el = (id) => doc.getElementById(id);
  return {
    escrituras,
    intervalos,
    timeouts,
    fetch,
    doc,
    el,
    // Enciende el intervalo dado (o todos) y deja que las promesas caigan.
    async disparar(ms) {
      const elegidos = ms == null ? intervalos : intervalos.filter((i) => i.ms === ms);
      elegidos.forEach((i) => i.fn());
      await flush();
      return elegidos.length;
    },
    async correrTimeouts() {
      const viejos = timeouts.splice(0, timeouts.length);
      viejos.forEach((t) => t.fn());
      await flush();
      return viejos.length;
    },
  };
}

/* --------------------------------------------------------------- escenarios */

// El reproductor se traga el <script> entero de una sola vez.
let CODIGO = '';
try {
  const html = fs.readFileSync(PAGINA, 'utf8');
  const m = html.match(/<script>([\s\S]*?)<\/script>/);
  if (!m) throw new Error('no hay bloque <script> sin atributos');
  CODIGO = m[1];
} catch (e) {
  console.log(`\n  ${ROJO}FALLA${FIN}  no pude leer el script de web/index.html: ${e.message}\n`);
  process.exit(1);
}

// El aire: el source que Icecast reporta cuando la radio esta al aire.
const AIRE = {
  server_name: 'Radio Privada',
  title: 'Steady Hand — Radio Privada',
  listeners: 3,
};

async function escenarioUnTick() {
  console.log('\nun solo tick de estado:');
  const p = await abrirPagina({ datos: AIRE });

  // Arranque: una verificacion inmediata, no dos.
  check(p.fetch.pedidos.length === 1, 'al cargar la pagina: exactamente 1 fetch de estado');
  check((p.escrituras.now || 0) === 1, 'al cargar la pagina: #now se escribe 1 vez');
  check(p.el('estado').textContent === 'live', 'al cargar: el cartel dice live');

  // Ningun otro intervalo del reproductor debe hablar con el endpoint.
  const queFetchean = [];
  for (const i of p.intervalos) {
    const antes = p.fetch.pedidos.length;
    i.fn();
    await flush();
    if (p.fetch.pedidos.length > antes) queFetchean.push(i.ms);
  }
  check(
    queFetchean.length === 1 && queFetchean[0] === 15000,
    `un solo intervalo consulta el estado (15000 ms); encontrados: [${queFetchean.join(', ')}]`
  );

  // Cada tick: +1 fetch, +1 escritura de #now, +1 escritura de estado.
  const antes = { ped: p.fetch.pedidos.length, now: p.escrituras.now || 0, est: p.escrituras.estado || 0 };
  await p.disparar(15000);
  check(p.fetch.pedidos.length - antes.ped === 1, '15 s: el tick hace exactamente 1 fetch');
  check((p.escrituras.now || 0) - antes.now === 1, '15 s: #now se escribe 1 vez');
  check((p.escrituras.estado || 0) - antes.est === 1, '15 s: el cartel se escribe 1 vez');

  const ahora = { ped: p.fetch.pedidos.length, now: p.escrituras.now || 0 };
  await p.disparar(15000);
  check(p.fetch.pedidos.length - ahora.ped === 1, 'otro tick: sigue siendo 1 fetch, no se acumulan');
  check((p.escrituras.now || 0) - ahora.now === 1, 'otro tick: #now, 1 vez de nuevo');

  check(p.el('now').textContent.trim() === AIRE.title, '#now muestra el title, no el server_name');
  check(p.doc.title === `${AIRE.title} — Radio`, 'el titulo del documento acompana al tema');
  check(p.el('oyentes').textContent === '3 listeners', 'los oyentes salen del mismo objeto');
}

async function escenarioCadenaManda() {
  console.log('\nla cadena de reconexion manda sobre el tick:');
  const p = await abrirPagina({ datos: AIRE });
  check(p.el('estado').textContent === 'live', 'arranca en live');

  // Un corte: el mismo camino que usaria el navegador (error de audio).
  p.el('audio').dispara('error');
  await flush();
  check(p.el('estado').textContent === 'reconectando', 'el corte pone reconectando');
  check(!p.el('dot').classList.contains('live'), 'el punto verde se apaga');
  check(p.timeouts.length > 0, 'el reintento quedo agendado');

  // Aca estaba el bug: el tick vivo pisaba "reconectando" con "live".
  const escritas = p.escrituras.estado || 0;
  const antes = p.fetch.pedidos.length;
  await p.disparar(15000);
  check(p.fetch.pedidos.length - antes === 1, 'el tick sigue corriendo (no se congela la pagina)');
  check(p.el('estado').textContent === 'reconectando', 'tick en vivo: el cartel NO pasa a live');
  check((p.escrituras.estado || 0) === escritas, 'tick en vivo: ni una escritura del cartel');

  // Y con el backend caido tampoco: "Radio off air" tampoco manda.
  p.fetch.alcanzable = false;
  await p.disparar(15000);
  check(p.el('estado').textContent === 'reconectando', 'tick sin backend: el cartel sigue intacto');

  // La cadena termina sola y el tick vuelve a mandar.
  await p.correrTimeouts();           // reintento agendado -> conectar()
  p.el('audio').dispara('playing');   // el stream volvio
  check(p.el('estado').textContent === 'live', 'vuelta del stream: live otra vez');
  p.fetch.alcanzable = true;
  await p.disparar(15000);
  check(p.el('estado').textContent === 'live', 'con la cadena cerrada, el tick vuelve a pintar');
}

async function escenarioDosFormas() {
  console.log('\nlas dos formas del endpoint pintan igual (F-15):');
  const objeto = await abrirPagina({ datos: AIRE });
  const array = await abrirPagina({ datos: [AIRE] });
  const dos = await abrirPagina({ datos: [AIRE, { server_name: 'segundo mount' }] });

  const leer = (p) => [
    p.el('now').textContent,
    p.el('oyentes').textContent,
    p.el('estado').textContent,
    p.el('aviso').style.display,
    p.doc.title,
  ].join(' | ');

  const a = leer(objeto);
  const b = leer(array);
  const c = leer(dos);
  check(a === b, `objeto e array dan el mismo render\n        objeto: ${a}\n        array:  ${b}`);
  check(b === c, 'con dos mounts se lee el primero, igual que con uno');
  check(/Radio Privada/.test(a) && /3 listeners/.test(a), 'el render trae title y listeners');
}

/* ------------------------------------------------------------------ corrida */

(async () => {
  console.log('\nUI gate — web/index.html');
  await escenarioUnTick();
  await escenarioCadenaManda();
  await escenarioDosFormas();

  console.log('');
  if (mal === 0) {
    console.log(`${VERDE}Todo bien${FIN} - ${ok} chequeos.\n`);
    process.exit(0);
  }
  console.log(`${ROJO}${mal} de ${ok + mal} chequeos fallan${FIN}\n`);
  process.exit(1);
})().catch((e) => {
  console.log(`\n  ${ROJO}FALLA${FIN}  el gate se rompio solo: ${e && e.stack ? e.stack : e}\n`);
  process.exit(1);
});
