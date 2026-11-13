  /*
    S11: the seed on stage. indiegenerator publishes a per-track sidecar next
    to each FLAC (`/music/<stem>.json`, schema "indiegen/sidecar/1") with
    lane, seed and plan_sha256. When the current track's sidecar is found, the
    radio renders `lane · seed · plan_sha256` under #now. When it is not (no
    manifest, network down, no match), the line stays hidden: never faked.
    The aggregated index `/music/catalog.json` is used to find the sidecar for
    the track that is playing, because the Icecast title does not carry the
    file name. Best effort, never throws: a missing manifest is a valid state.
  */
  var catalogo = null;
  var catalogoCargando = false;
  var catalogoFallido = false;
  function normalizarTitulo(s) {
    return String(s || '').toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
  }
  function buscarEntradaCatalogo(titulo) {
    if (!catalogo || !Array.isArray(catalogo.entries || catalogo)) return null;
    var lista = catalogo.entries || catalogo;
    var clave = normalizarTitulo(titulo);
    if (!clave) return null;
    var palabras = clave.split(/\s+/).filter(Boolean);
    for (var i = 0; i < lista.length; i++) {
      var e = lista[i];
      var ruta = String(e.path || e.file || '');
      var base = normalizarTitulo(ruta.replace(/\.flac$/i, ''));
      if (!base) continue;
      // Match: every word of the title appears in the file stem, or vice versa.
      // The Icecast title carries the station suffix ("Radio Comechingona") that is
      // not part of the file name: require only that at least one significant
      // word of the title (4+ letters) appears in the file stem.
      var significativas = palabras.filter(function (w) { return w.length >= 4; });
      if (significativas.length === 0) continue;
      var coincide = significativas.some(function (w) { return base.indexOf(w) !== -1; });
      if (coincide) return e;
    }
    return null;
  }
  function ocultarSeed() {
    if (seed) { seed.hidden = true; seed.textContent = ''; }
     if (seedCopy) { seedCopy.hidden = true; }
     if (seedHint) { seedHint.hidden = true; }
  }
  function cargarManifiesto(titulo) {
    if (!seed) return;
    if (!titulo) { ocultarSeed(); return; }
    var mostrar = function (sidecar) {
      if (!sidecar || !sidecar.lane || sidecar.seed == null || !sidecar.plan_sha256) {
        ocultarSeed(); return;
      }
      var nombrePista = (sidecar.alias || (sidecar.plan && sidecar.plan.alias) || sidecar.lane);
      var textoSeed = nombrePista + ' \\u00b7 ' + sidecar.seed + ' \\u00b7 ' + sidecar.plan_sha256;
      seed.textContent = textoSeed;
      seed.hidden = false;
      if (seedCopy) { seedCopy.textContent = STR.copySeed; seedCopy.hidden = false; seedCopy.onclick = function () {
        var copiar = (typeof navigator !== 'undefined' && navigator.clipboard && navigator.clipboard.writeText) ? navigator.clipboard.writeText(textoSeed) : Promise.resolve();
        copiar.then(function () {
          if (seedCopy) { seedCopy.textContent = STR.seedCopied; setTimeout(function () { if (seedCopy) seedCopy.textContent = STR.copySeed; }, 1200); }
        }).catch(function () {});
      }; }
      if (seedHint) { seedHint.textContent = STR.seedHint; seedHint.hidden = false; }
    };
    var entrada = buscarEntradaCatalogo(titulo);
    if (entrada && entrada.lane && entrada.seed != null) {
      // The catalog already has lane + seed; fetch the sidecar only for plan_sha256.
      var ruta = String(entrada.path || entrada.file || '');
      var sidecarUrl = BACKEND_URL + '/music/' + ruta.replace(/\.flac$/i, '.json');
      fetch(sidecarUrl, OPCIONES_ESTADO)
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (j) {
          if (j && j.plan_sha256) mostrar(j);
          else if (entrada.lane && entrada.seed != null) mostrar({ lane: entrada.lane, alias: entrada.alias, seed: entrada.seed, plan_sha256: (entrada.plan_sha256 || '') });
          else ocultarSeed();
        })
        .catch(ocultarSeed);
      return;
    }
    // No catalog yet: try to load it once, then retry.
    if (catalogoFallido) { ocultarSeed(); return; }
    if (!catalogo && !catalogoCargando) {
      catalogoCargando = true;
      fetch(BACKEND_URL + '/music/catalog.json', OPCIONES_ESTADO)
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (j) {
          catalogo = j;
          catalogoCargando = false;
          if (!catalogo) { catalogoFallido = true; ocultarSeed(); return; }
          cargarManifiesto(titulo);
        })
        .catch(function (e) { catalogoCargando = false; catalogoFallido = true; ocultarSeed(); });
      return;
    }
    ocultarSeed();
  }

  function sonando() {
    return !audio.paused && audio.readyState >= 3;
  }

  btn.addEventListener('click', function () {
    /*
      A click belongs to the listener, not to the chain: what is scheduled is
      thrown away and the budget is renewed (F-17/F-18). Without this, the
      scheduled retry started the radio after he had turned it off, and a
      manual retry after the five attempts were spent had none at all: it died
      on the first failure.
    */
    cancelarReintento();
    reintentando = false;
    reintentos = 0;
    if (sonando()) {
      pausadoAMano = true;
      audio.pause();
      return;
    }
    pausadoAMano = false;
    conectar(true);
  });
  audio.addEventListener('playing', function () {
    reintentos = 0;
    reintentando = false;
    pausadoAMano = false;
    bloqueadoPorAutoplay = false;
    reproduciendo = true;
    btn.innerHTML = STR.pauseIcon;
    setLabel(STR.pause);
    estado.textContent = STR.live;
    dot.classList.add('live');
    err.style.display = 'none';
  });
  audio.addEventListener('pause', function () {
    btn.innerHTML = STR.playIcon;
    setLabel(STR.play);
    // The `pause` fired by every failed attempt must not paint over the
    // banner: while a chain is in flight the page is reconnecting, not
    // stopped (without this the UI contradicted the "Reconnecting (n/5)"
    // banner).
    if (!reintentando) estado.textContent = STR.stopped;
    dot.classList.remove('live');
  });
  audio.addEventListener('error', function () {
    ultimoCodigo = audio.error ? audio.error.code : 0;
    reproduciendo = false;
    alFallar();
  });

  /*
    Clean server close: if Icecast kicked the connection (source gone for
    longer than its timeout), Chrome sees the stream end and NOT an error.
    Without this listener the page stays mute in "stopped" forever: there is no
    error to call alFallar() and the watchdog stops as soon as paused goes
    true. Measured with a liquidsoap pause: ended at 11s and the page did not
    come back on its own, not even with the source restored.

    A stream end enters through the same question as an error: did the
    credential fall or was it the network? alFallar() already decides that.
    ultimoCodigo=0 because a clean close carries no error code (a previous
    cut's stale code would confuse the final message).
  */
  audio.addEventListener('ended', function () {
    ultimoCodigo = 0;
    reproduciendo = false;
    alFallar();
  });

  function falloDefinitivo() {
    // The chain is released. The fresh budget comes from the listener's click
    // (see the button handler): a manual retry starts at (1/5), it does not
    // hit an exhausted counter (F-17).
    reintentando = false;
    cancelarReintento();
    err.style.display = 'block';
    err.innerHTML = sinCredencial
      ? STR.door
      : STR.connectFailed(ultimoCodigo);
    dot.classList.remove('live');
    estado.textContent = STR.error;
  }

  /*
    Watchdog. A network cut does not always fire `error`: Chrome sits in
    readyState 2 with `paused:false` waiting for data that never arrives,
    forever. Verified by cutting Caddy mid-playback.

    CAREFUL at start-up: currentTime is 0 while it buffers, and that is NOT a
    cut. That is why it only watches after the first `playing`; counting from
    the start would cut every healthy playback at 3s.
  */
  var ultimoT = 0;
  var quieto = 0;
  var reproduciendo = false;

  setInterval(function () {
    if (!reproduciendo || audio.paused) {
      ultimoT = audio.currentTime;
      quieto = 0;
      return;
    }
    if (audio.currentTime > ultimoT + 0.01) {
      ultimoT = audio.currentTime;
      quieto = 0;
      return;
    }
    quieto++;
    if (quieto >= 3) {
      quieto = 0;
      reproduciendo = false;
      ultimoT = audio.currentTime;
      alFallar();
    }
  }, 1000);

