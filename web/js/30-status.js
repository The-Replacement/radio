  /*
    ------------------------------------------------------------------
    One status tick.
    ------------------------------------------------------------------

    There used to be TWO polls in parallel (15 s each) against the same
    endpoint, and both wrote #now — one with server_name, the other with
    title: the last answer won and the caption could alternate between
    "Radio Comechingona" and the track. The poll that did not know about the
    reconnection chain also painted "live" over "reconnecting" in the middle
    of a retry: the `pause` handler had already been cured of that class of
    bug (:433-441), the poll had not.

    Now: one fetch, one verdict, one render(). And TWO priority rules:

      1. THE CHAIN OUTRANKS THE TICK. While reintentando === true the tick
         measures but does not paint: the whole caption belongs to whoever is
         fixing the cut.
      2. NO DATA, NO PAINT. Every way of failing means something different;
         the previous code read them all as one ("Radio off air") and disabled
         the button with the audio still playing.
  */
  var TICK_MS = 15000;

  /*
    ------------------------------------------------------------------
    Does the cookie work? — the question that decides whether the token
    keeps travelling
    ------------------------------------------------------------------

    With ?t= in the URL, the first status request is made WITHOUT the
    suffix. Three answers, three decisions:

      200  -> the cookie opens. The token stops travelling: sufijo = '' and
              the address bar is cleaned. Neither the stream, nor the status,
              nor WHEP spend it again.
      404  -> the cookie does not open. The token keeps travelling in sufijo
              — in memory, never in the address — and the bar is cleaned
              anyway (F-01: principle 6 is about the address, not the
              fallback). The second request, with the token, decides the
              verdict: 200 = the fallback holds the radio up, 404 = the link
              died too (the door).
      other (502, network) -> proves nothing. Neither the suffix nor the bar
              is touched and the question repeats on the next tick: deciding
              with the radio down would throw the fallback into the void.

    Until the cookie is proven, sufijo is still the one that came in the URL:
    asking with it is not a mistake, it is the previous state.
  */
  var cookieProbada = false;

  function pedidoDeEstado() {
    var conT = BACKEND_URL + '/status-json.xsl' + sufijo;
    if (!t || cookieProbada) return fetch(conT, OPCIONES_ESTADO);

    return fetch(BACKEND_URL + '/status-json.xsl', OPCIONES_ESTADO)
      .then(function (r) {
        if (r.status === 404) {
          // The 404 came WITHOUT the token: the cookie is already decided.
          // The bar is cleaned here, before the second request — even if that
          // one fails, the decision is not left pending.
          cookieProbada = true;
          limpiarBarra();
          return fetch(conT, OPCIONES_ESTADO);
        }
        if (r.status === 200) {
          cookieProbada = true;
          sufijo = '';
          limpiarBarra();
        }
        return r;
      });
  }

  function leerEstado() {
    return pedidoDeEstado()
      .then(function (r) {
        // 404 = THE DOOR closed (Caddy `respond 404`): it is the credential,
        // not the radio. It is the same discrimination alFallar already made
        // (:344) — the code knew it, it was missing a reader that believed
        // it.
        if (r.status === 404) return { veredicto: 'puerta' };
        // The gate never returns 401/403 (principle 1): only 404 and 200.
        // Any other answer is proxy or tunnel noise, and means neither "off
        // air" nor "dead credential".
        if (!r.ok) return { veredicto: 'sin-datos' };
        // A 200 with an unreadable body falls into the catch below: same
        // verdict as a dead network, for the same reason.
        return r.json().then(function (data) {
          // Icecast publishes the active mount in icestats.source. No
          // source, no air (liquidsoap down or with no music).
          // The shape of the data depends on who answers: object (one mount)
          // or array (more than one). It is normalised ONCE here: with two
          // reads in two functions, an array showed "Radio" and "0 listeners"
          // on one side and the title on the other.
          // CAREFUL: listeners=0 does NOT mean stopped; it is people
          // listening right now.
          var s = data && data.icestats && data.icestats.source;
          if (Array.isArray(s)) s = s[0] || null;
          return { veredicto: s ? 'aire' : 'sin-aire', fuente: s };
        });
      })
      .catch(function () {
        // Dead network, box off, tunnel down: the page knows NOTHING about
        // the radio or the door, so it claims nothing. "Radio off air" here
        // was the lie of F-04: the audio could still be playing with the
        // off-air banner and a dead button.
        return { veredicto: 'sin-datos' };
      });
  }

  function render(f) {
    // Rule 1: while the chain is fixing a cut, the tick does not paint. The
    // fetch went out anyway (the measurement is not lost), but the caption
    // belongs to reintentar()/falloDefinitivo(): two owners writing at once is
    // what made "reconnecting" fight "live".
    if (reintentando) return;

    // Rule 1b: the browser blocked autoplay. The air may exist —it answered
    // 200, there is a source— but this browser will not start without a
    // gesture, and the server does not know that. While the block is alive
    // the tick does not paint: the block's caption rules until a click (a
    // gesture) resolves it.
    if (bloqueadoPorAutoplay) return;

    // Rule 2: no data, no verdict, and the last one stands. Erasing it would
    // also be claiming something we do not know.
    if (f.veredicto === 'sin-datos') return;

    var s = f.fuente || null;

    if (f.veredicto === 'puerta') {
      // The credential died: the whole ritual runs from the tick, so it
      // arrives even if the listener never pressed play — that was the case
      // where the button was disabled and the only sentence that mattered
      // could never be seen.
      err.style.display = 'block';
      err.innerHTML = STR.door;
      aviso.style.display = 'none';
      if (toast) {
        toast.textContent = STR.doorToast;
        toast.hidden = false;
        setTimeout(function () { if (toast) toast.hidden = true; }, 4000);
      }
      dot.classList.remove('live');
      estado.textContent = STR.error;
      btn.disabled = true;
      ocultarSeed();
      return;
    }

    if (f.veredicto === 'aire') {
      var titulo = (s && (s.title || s.yp_currently_playing)) || '';
      // #now has ONE writer, this block. The value comes from a single
      // field (title) with a fallback: two polls used to write it with
      // different fields and the caption flickered.
      now.textContent = (titulo || s.server_name || s.server_description || STR.station) + '\u00a0';
      if (titulo) document.title = titulo + ' — ' + STR.station;
      cargarManifiesto(titulo);
      aviso.style.display = 'none';
      btn.disabled = false;
      dot.classList.add('live');
      estado.textContent = STR.live;
      if (sonando()) { btn.innerHTML = STR.pauseIcon; setLabel(STR.pause); }
    } else {
      // 'sin-aire': it answered 200 and there is no source. This is known.
      now.textContent = '';
      ocultarSeed();
      aviso.style.display = 'block';
      aviso.textContent = STR.broadcastStopped;
      btn.disabled = true;
      dot.classList.remove('live');
      estado.textContent = STR.stopped;
    }
  }

  function tick() { leerEstado().then(render); }

  // Immediate check on page load, then every TICK_MS.
  tick();
  setInterval(tick, TICK_MS);

