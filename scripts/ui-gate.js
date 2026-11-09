#!/usr/bin/env node
/*
 * ui-gate.js — mide la estructura del estado del reproductor.
 *
 *   ./scripts/ui-gate.js
 *
 *   ./scripts/ui-gate.js              corre los escenarios
 *   ./scripts/ui-gate.js --self-test  demuestra que el gate puede fallar
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
 *   1  hay al menos una falla (o el self-test detecto un gate sin dientes)
 *
 * Escenarios:
 *   S1  un solo tick: un fetch, un escritor de #now, la cadena manda.
 *   S1  las dos formas del endpoint (objeto/array) pintan igual.  [F-15]
 *   S2  la matriz 200 / 404 / rechazo de red al cargar.           [F-03]
 *   S2  una falla transitoria no apaga la radio.                  [F-04]
 *   S2  la copia de la puerta es UNA sola, y la escriben los dos
 *       caminos que pueden descubrir el 404.
 *
 * Self-test (ver issue #23): un gate verde no prueba nada si no se lo vio
 * fallar. Rompe el codigo en memoria —una garantía por mutación— y exige
 * rojos en cada una, y verdes en la página real. Si una mutación deja el
 * gate en verde, el gate dejó de medir eso: también sale rojo.
 */
'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const RAIZ = path.join(__dirname, '..');
const PAGINA = path.join(RAIZ, 'web', 'index.html');

// Ids que la pagina declara de verdad. El DOM inventado del gate sólo
// responde esos: probar contra un id que no existe es probar contra
// nadie.
const IDS = new Set();

/* --------------------------------------------------------------- utilidades */

const VERDE = '\x1b[32m';
const ROJO = '\x1b[31m';
const FIN = '\x1b[0m';

let ok = 0;
let mal = 0;
// Etiquetas de las que salieron rojas: el self-test las muestra como
// prueba de que la mutación dolió donde tenía que doler.
let rojas = [];
// El self-test corre el gate una vez más por mutación: sin esto la
// salida sería 6 x 51 líneas de ruido.
let verbose = true;
const pasa = (n) => { if (verbose) console.log(`  ${VERDE}OK${FIN}    ${n}`); ok++; };
const falla = (n) => {
  if (verbose) console.log(`  ${ROJO}FALLA${FIN}  ${n}`);
  mal++;
  rojas.push(n);
};
const check = (cond, nombre) => (cond ? pasa(nombre) : falla(nombre));
// Titulos de escenario: mismos switch que los chequeos, para que el
// self-test pueda correr el gate entero en silencio.
const titular = (t) => { if (verbose) console.log(t); };

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
    _clases: clases,
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
      // Si el gate (o el codigo) piden un id que no existe en la pagina,
      // se prueba contra un elemento fantasma y el chequeo pasa en vano:
      // es exactamente como se cae un gate sin darse cuenta.
      if (!IDS.has(id)) throw new Error('id que no existe en web/index.html: ' + id);
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
    // El objeto que el fetch lee: se puede cambiar en vivo para que el
    // proximo tick vea otra realidad (404, red caida, 502...).
    config: fetchFuente,
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
  for (const id of html.matchAll(/\sid="([^"]+)"/g)) IDS.add(id[1]);
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
  titular('\nun solo tick de estado:');
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
  titular('\nla cadena de reconexion manda sobre el tick:');
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
  p.config.alcanzable = false;
  await p.disparar(15000);
  check(p.el('estado').textContent === 'reconectando', 'tick sin backend: el cartel sigue intacto');

  // La cadena termina sola y el tick vuelve a mandar.
  await p.correrTimeouts();           // reintento agendado -> conectar()
  p.el('audio').dispara('playing');   // el stream volvio
  check(p.el('estado').textContent === 'live', 'vuelta del stream: live otra vez');
  p.config.alcanzable = true;
  await p.disparar(15000);
  check(p.el('estado').textContent === 'live', 'con la cadena cerrada, el tick vuelve a pintar');
}

async function escenarioDosFormas() {
  titular('\nlas dos formas del endpoint pintan igual (F-15):');
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

/* ------------------------------------------------- la puerta y el aire */

// La frase de la puerta, tal cual debe verse en pantalla (sin el <br>).
const FRASE_PUERTA = 'Server replied 404.\n' +
  'Access expired or the browser did not save the cookie. Ask for a new link.';

const verErr = (p) => String(p.el('err').innerHTML || '').replace(/<br\s*\/?>/gi, '\n');

// "No pintó nada": qué superficies del tick fueron tocadas (vacío = ninguna).
function fallasDePintura(p) {
  const f = [];
  if (p.escrituras.estado) f.push(`el estado se escribio ${p.escrituras.estado} vez/veces`);
  if (p.escrituras.now) f.push(`#now se escribio ${p.escrituras.now} vez/veces`);
  if ('display' in p.el('err').style) f.push('el cartel de error fue tocado');
  if ('display' in p.el('aviso').style) f.push('el aviso fue tocado');
  if (p.el('play').disabled) f.push('el boton quedo deshabilitado');
  if (p.el('dot')._clases.size) f.push('el punto fue tocado: ' + [...p.el('dot')._clases].join(','));
  return f;
}
const intacto = (p) => fallasDePintura(p).length === 0;
const dondeFalla = (p) => fallasDePintura(p).join(' / ');

async function escenarioMatriz() {
  titular('\nla matriz del tick al cargar (F-03 / F-04):');

  const aire = await abrirPagina({ datos: AIRE });
  check(aire.el('estado').textContent === 'live' && aire.el('play').disabled === false,
    '200 con source -> live, boton habilitado');
  check(aire.el('aviso').style.display === 'none' && aire.el('err').style.display !== 'block',
    '200 con source -> sin avisos');
  check(/Radio Privada/.test(aire.el('now').textContent), '200 con source -> #now con el tema');

  const sinAire = await abrirPagina({ datos: null });
  check(sinAire.el('aviso').textContent === 'Broadcast is stopped'
        && sinAire.el('aviso').style.display === 'block',
    '200 sin source -> "Broadcast is stopped"');
  check(sinAire.el('estado').textContent === 'stopped' && sinAire.el('play').disabled === true,
    '200 sin source -> stopped y boton muerto');

  // ESTA es la fila que no existia: la puerta cerrada hablando en el
  // momento en que mas importa — al cargar, sin haber apretado play.
  const puerta = await abrirPagina({ codigo: 404 });
  check(verErr(puerta) === FRASE_PUERTA,
    `404 -> la frase exacta de la puerta\n        ${verErr(puerta).replace(/\n/g, ' / ')}`);
  check(puerta.el('err').style.display === 'block', '404 -> el cartel de la puerta se ve');
  check(puerta.el('aviso').style.display !== 'block', '404 -> NO dice "Broadcast is stopped"');
  check(puerta.el('estado').textContent === 'error', '404 -> el estado dice error, no stopped');
  check(puerta.el('play').disabled === true, '404 -> boton muerto (no hay nada que reproducir)');
  check(/Ask for a new link/.test(verErr(puerta)),
    '404 -> "Ask for a new link" esta a la vista al cargar');

  const rechazo = await abrirPagina({ alcanzable: false });
  check(rechazo.fetch.pedidos.length === 1, 'red caida -> igual hace el fetch (mide, no afirma)');
  check(intacto(rechazo),
    `red caida -> no pinta nada: ni off air, ni puerta, ni boton muerto${intacto(rechazo) ? '' : `\n        ${dondeFalla(rechazo)}`}`);

  const error502 = await abrirPagina({ codigo: 502 });
  check(intacto(error502),
    `502 del tunel -> tampoco miente${intacto(error502) ? '' : `\n        ${dondeFalla(error502)}`}`);
}

async function escenarioTransitoria() {
  titular('\nuna falla transitoria no apaga la radio (F-04):');
  const p = await abrirPagina({ datos: AIRE });
  p.el('audio').dispara('playing'); // el audio esta sonando de verdad
  check(p.el('estado').textContent === 'live' && p.el('play').disabled === false,
    'arranca live, boton habilitado');

  p.config.alcanzable = false;
  const antes = p.fetch.pedidos.length;
  await p.disparar(15000);
  check(p.fetch.pedidos.length - antes === 1, 'el tick consulta igual: midio, no se quedo mudo');
  check(p.el('estado').textContent === 'live', 'el estado sigue en live');
  check(p.el('play').disabled === false, 'el boton sigue habilitado');
  check(p.el('aviso').style.display !== 'block', 'no aparece ningun aviso');
  check(!/off air/i.test(p.el('aviso').textContent + p.el('err').innerHTML),
    'ni el aviso ni el cartel dicen "off air"');
  check(p.el('dot').classList.contains('live'), 'el punto verde sigue encendido');

  p.config.alcanzable = true;
  await p.disparar(15000);
  check(p.el('estado').textContent === 'live', 'red de vuelta -> el tick retoma el pintado solo');
}

async function escenarioPuerta() {
  titular('\nla puerta, por los dos caminos que la descubren:');

  // Camino 1: la cadena. El token vence a mitad de sesion, el stream
  // cae, alFallar() le pregunta al gate y el gate dice 404.
  const cadena = await abrirPagina({ datos: AIRE });
  cadena.config.codigo = 404;
  cadena.el('audio').dispara('error');
  await flush();
  check(cadena.el('err').style.display === 'block', 'la cadena llego hasta falloDefinitivo');
  check(verErr(cadena) === FRASE_PUERTA, 'la cadena escribe la MISMA frase');

  // Camino 2: el tick. Nunca hubo audio: solo la pagina y su 404.
  const tick = await abrirPagina({ codigo: 404 });
  check(verErr(tick) === verErr(cadena), 'los dos caminos no pueden divergir');

  // Y no pueden divergir en el futuro: la frase esta escrita una vez.
  const veces = CODIGO.split('Access expired or the browser did not save').length - 1;
  check(veces === 1, `la frase de la puerta esta escrita una sola vez en el codigo (${veces})`);
}

/* ------------------------------------------------------------------ corrida */

async function correrEscenarios() {
  ok = 0; mal = 0; rojas = [];
  await escenarioUnTick();
  await escenarioCadenaManda();
  await escenarioDosFormas();
  await escenarioMatriz();
  await escenarioTransitoria();
  await escenarioPuerta();
  return { ok, mal, rojas: rojas.slice() };
}

/* ------------------------------------------------------------- self-test */

/*
  Una mutación por garantía que el gate dice custodiar. Cada una rompe
  exactamente una regla escrita en web/index.html y nada más. La rotura
  es sobre el string en memoria: el archivo de la página no se toca, así
  que no hay nada que restaurar y ninguna chance de dejar la radio
  mutada en el repositorio.

  Ver issue #23: un gate que nunca se vio fallar no está verificado,
  está creído.
*/
const MUTACIONES = [
  {
    que: 'un segundo tick de 15 s (la regla de un solo fetch por tick)',
    aplica: (h) => h.replace('setInterval(tick, TICK_MS);',
      'setInterval(tick, TICK_MS); setInterval(tick, TICK_MS);'),
  },
  {
    que: 'la cadena deja de mandar sobre el tick',
    aplica: (h) => h.replace(/if \(reintentando\) return;\n\n    \/\/ Regla 2/,
      '// (guarda mutada)\n\n    // Regla 2'),
  },
  {
    que: 'el 404 se lee como cualquier otra respuesta',
    aplica: (h) => h.replace("if (r.status === 404) return { veredicto: 'puerta' };",
      "if (r.status === 401) return { veredicto: 'puerta' };"),
  },
  {
    que: 'sin datos -> vuelve a pintar (la mentira de la falla transitoria)',
    aplica: (h) => h.replace("if (f.veredicto === 'sin-datos') return;",
      "if (f.veredicto === 'nunca') return;"),
  },
  {
    que: 'las dos formas del endpoint se leen distinto',
    aplica: (h) => h.replace('if (Array.isArray(s)) s = s[0] || null;', ''),
  },
  {
    que: 'la frase de la puerta se escribe dos veces',
    aplica: (h) => h.replace('var PUERTA = ',
      "var PUERTA_COPIA = 'Access expired or the browser did not save the cookie.';\n  var PUERTA = "),
  },
];

const sinAnsi = (s) => String(s).replace(/\x1b\[[0-9;]*m/g, '');
const primeraLinea = (s) => sinAnsi(s).split('\n')[0].trim();

async function selfTest() {
  const original = CODIGO;
  // Corre el gate sin ruido y siempre devuelve el codigo a la página real.
  const correrCallado = async () => {
    verbose = false;
    try { return await correrEscenarios(); }
    finally { verbose = true; CODIGO = original; }
  };

  titular('\nSelf-test — ¿puede fallar este gate?');
  console.log('');

  let problemas = 0;

  const real = await correrCallado();
  if (real.mal === 0) {
    console.log(`  ${VERDE}OK${FIN}    pagina real .......................... ${real.ok}/${real.ok} en verde`);
  } else {
    console.log(`  ${ROJO}FALLA${FIN}  la pagina real tiene ${real.mal} rojos: arreglalos antes de creerle al self-test`);
    problemas++;
  }

  for (const m of MUTACIONES) {
    const mutado = m.aplica(original);
    if (mutado === original) {
      // El codigo cambio de forma y la mutacion ya no sabe donde
      // cortar: eso NO es un pase, es un gate ciego.
      console.log(`  ${ROJO}FALLA${FIN}  la mutacion no aplico: ${m.que}`);
      problemas++;
      continue;
    }
    CODIGO = mutado;
    const r = await correrCallado();
    if (r.mal === 0) {
      console.log(`  ${ROJO}FALLA${FIN}  quedo en verde con: ${m.que}`);
      problemas++;
    } else {
      console.log(`  ${VERDE}OK${FIN}    ${m.que}`);
      console.log(`          ${r.mal} rojos, p.e. "${primeraLinea(r.rojas[0])}"`);
    }
  }

  console.log('');
  if (problemas === 0) {
    console.log(`${VERDE}Todo bien${FIN} - ${MUTACIONES.length} mutaciones atrapadas.\n`);
    process.exit(0);
  }
  console.log(`${ROJO}${problemas} problema(s) en el self-test${FIN}\n`);
  process.exit(1);
}

(async () => {
  if (process.argv.includes('--self-test')) { await selfTest(); return; }

  titular('\nUI gate — web/index.html');
  const r = await correrEscenarios();

  console.log('');
  if (r.mal === 0) {
    console.log(`${VERDE}Todo bien${FIN} - ${r.ok} chequeos.\n`);
    process.exit(0);
  }
  console.log(`${ROJO}${r.mal} de ${r.ok + r.mal} chequeos fallan${FIN}\n`);
  process.exit(1);
})().catch((e) => {
  console.log(`\n  ${ROJO}FALLA${FIN}  el gate se rompio solo: ${e && e.stack ? e.stack : e}\n`);
  process.exit(1);
});
