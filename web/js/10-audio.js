  /*
    Automatic reconnection. A live stream cuts: the network changes, the
    machine sleeps, a proxy recycles a connection. Before this the page gave
    the definitive error at the first hiccup, and that is what listeners saw:
    the audio connected, played for a few seconds, and on the cut the banner
    said "ask for a new link" — when the link was perfect.
  */
  var reintentos = 0;
  var MAX_REINTENTOS = 5;

  /*
    Bound for the credential poll (alFallar). Unbounded, a request that never
    comes back —black-holed connection, no RST: exactly the failure mode the
    watchdog exists for— leaves reintentando = true forever: the tick, `ended`
    and `error` all go mute, with no banner and no counter, and the page looks
    alive without being it (F-09). The bound turns that silence into the answer
    we already know how to read: network.

    Measured on 2026-11-10, 40 requests to /status-json.xsl per path:
      · loopback on the mainframe (server only: the token matcher costs
        ~190 ms of CPU) -> p50 193 ms, p95 195 ms, max 198 ms.
      · from a desktop machine through the public tunnel
        (Cloudflare -> home) -> p50 168 ms, p95 333 ms, max 367 ms.
    3000 ms is 9x the real path's p95 and 8x its maximum; it also lands in the
    same order as the watchdog's window (3 s frozen), the other detector of the
    same cut. Not a number from a blog.
  */
  var PROBE_MS = 3000;

  var ultimoCodigo = 0;
  var sinCredencial = false;
  // Is a reconnection chain in flight? See alFallar().
  var reintentando = false;
  // Timer for the next scheduled attempt. One only, and cancellable: the
  // listener's hand throws it away (F-18 — the chain does not start the radio
  // over his head).
  var relojReintento = null;
  // The listener turned the radio off by hand. A later `error` —a paused
  // element can emit one too— does not revive it: off is off.
  var pausadoAMano = false;
  // Did the browser block autoplay and there was no click with a gesture yet?
  // The network cannot unblock it: the tick does not paint over this state
  // (rule 1 has a local cousin — rule 0, so to speak).
  var bloqueadoPorAutoplay = false;

  /*
    The door's sentence lives in STR.door: it is written by the two paths that
    can discover it — the chain (falloDefinitivo) and the tick (render) —
    because they are the same fact read twice. If it ever shows up anywhere
    else, something broke (ui-gate counts it).
  */

  // The button's label travels with the state: Play when stopped, Pause when
  // playing. The glyph changes with it — a blind listener hears this change
  // through aria-live on the status block (F-11).
  function setLabel(label) { btn.setAttribute('aria-label', label); }

  function conectar(autoplay) {
    audio.crossOrigin = 'use-credentials';
    audio.src = BACKEND_URL + '/stream' + sufijo;
    if (!autoplay) return;
    audio.play().catch(function (e) {
      // play() rejects if the request fails, or if the browser blocks
      // autoplay. The second case is not fixed by retrying: the click fixes
      // it — and the listener has to know WHY it is not playing, not a mute
      // "stopped" or an eternal "reconnecting" (F-16).
      if (e && e.name === 'NotAllowedError') autoplayBloqueado();
    });
  }

  /*
    The browser blocked autoplay: not network, not credential — a policy. It
    is announced as what it is, with the button alive so that a click resolves
    it. Neither "stopped" nor silence (F-16).
  */
  function autoplayBloqueado() {
    bloqueadoPorAutoplay = true;
    aviso.style.display = 'block';
    aviso.textContent = STR.autoplayBlocked;
    estado.textContent = STR.blocked;
    dot.classList.remove('live');
    btn.innerHTML = STR.playIcon;
    setLabel(STR.play);
  }

  /*
    Before deciding whether retrying is worth anything, it asks the same gate
    for another resource. If /status-json.xsl also returns 404, the credential
    is the problem and retrying is pointless. If it answers, the cut was the
    network or the server, and retrying is worth it. Without this, a Caddy
    restarting was mistaken for an expired token: the browser gets 404 in both
    cases.
  */
  function alFallar() {
    /*
      One chain per cut. For the SAME incident several triggers can pile up —
      the watchdog (frozen time), an `ended` (Icecast kicks the connection),
      the retry's own `error` — and each one would call reintentar() on its
      own: without this guard the budget of 5 burns in ~5s instead of the ~12s
      of backoff, and a source that comes back at 8s arrives with the counter
      exhausted.
      Measured: kick at 11s with watchdog+ended firing together -> definitive
      failure before the source came back.

      CAREFUL: the chain ADVANCES through each attempt's events (fail ->
      error/ended -> alFallar -> next attempt), so the guard cannot stay on: it
      opens as soon as the scheduled retry runs (see reintentar()). Kept on,
      the chain dies stuck at (1/5) — measured too.
    */
    if (reintentando) return;
    // The listener turned it off by hand: the chain does not revive what he
    // turned off (F-18). The Play click turns it back on.
    if (pausadoAMano) return;
    reintentando = true;
    /*
      The poll cuts itself at PROBE_MS. An abort does NOT prove anything about
      the credential (below it lands in the same `catch` as the network): it
      reads as "no answer", which is the truth, and the chain goes on. While it
      was unbounded, this fetch was the only point in the page able to freeze
      it forever (F-09).
    */
    var corte = typeof AbortController === 'function' ? new AbortController() : null;
    var reloj = corte ? setTimeout(function () { corte.abort(); }, PROBE_MS) : null;
    var opciones = corte
      ? Object.assign({}, OPCIONES_ESTADO, { signal: corte.signal })
      : OPCIONES_ESTADO;
    fetch(BACKEND_URL + '/status-json.xsl' + sufijo, opciones)
      .then(function (r) { sinCredencial = r.status === 404; })
      .catch(function () { sinCredencial = false; })
      .then(function () {
        // The request already came back: the scheduled abort has nobody to cut.
        if (reloj !== null) clearTimeout(reloj);
        if (sinCredencial) { falloDefinitivo(); return; }
        reintentar();
      });
  }

  /*
    The only scheduled attempt there is. Cancelling it is what makes a
    listener's click a decision and not one more attempt: without it the chain
    starts the audio over his hand (F-18).
  */
  function cancelarReintento() {
    if (relojReintento === null) return;
    clearTimeout(relojReintento);
    relojReintento = null;
  }

  function reintentar() {
    reintentos++;
    if (reintentos > MAX_REINTENTOS) { falloDefinitivo(); return; }
    err.style.display = 'block';
    err.textContent = STR.reconnectingLine(reintentos, MAX_REINTENTOS);
    estado.textContent = STR.reconnecting;
    dot.classList.remove('live');
    relojReintento = setTimeout(function () {
      // The attempt runs: with this, the events of ITS failure (error or
      // ended) open alFallar() again and the chain goes on. While it was
      // scheduled, the guard held back the duplicate triggers of the same
      // cut.
      relojReintento = null;
      reintentando = false;
      conectar(true);
    }, 800 * reintentos);
  }

  /*
    Is it really playing? `audio.paused` lies during a cut: Chrome stays in
    readyState 2 with paused:false waiting for data that never arrives (the
    same state the watchdog below describes). If the button trusts paused, a
    cut leaves the element "not paused" and the click calls pause() instead of
    reconnecting: a dead button until a manual reload.

    Really playing is !paused AND with fresh data to go on (readyState >= 3,
    HAVE_FUTURE_DATA). On a live stream that only happens when data is truly
    flowing; at 0-2 it is not playing and reconnecting is the right move.
  */
