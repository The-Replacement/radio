  var audio = document.getElementById('audio');
  var btn = document.getElementById('play');
  var dot = document.getElementById('dot');
  var estado = document.getElementById('estado');
  // listeners count removed: a secret room does not post attendance.
  var now = document.getElementById('now');
  var seed = document.getElementById('seed');
  var seedCopy = document.getElementById('seed-copy');
  var seedHint = document.getElementById('seed-hint');
  var camCaption = document.getElementById('cam-caption');
  var toast = document.getElementById('toast');
  var err = document.getElementById('err');
  var aviso = document.getElementById('aviso');
  var tel = document.getElementById('tel');

  /*
    The station's copy, written once. Every visible string (and both
    icons) comes from here: if a sentence is written anywhere else,
    something broke. One voice, one place — D-2 / F-10 / F-11.
  */
  var STR = {
    station: 'comechingona',
    playIcon: '&#9654;',
    pauseIcon: '&#10073;&#10073;',
    play: 'Reproducir',
    pause: 'Pausar',
    live: 'en vivo',
    stopped: 'detenido',
    reconnecting: 'reconectando',
    error: 'error',
    blocked: 'bloqueado',
    broadcastStopped: 'La transmisión está detenida',
    autoplayBlocked: 'El navegador bloqueó la reproducción automática. Presioná reproducir.',
    door: 'El servidor respondió 404.<br>El acceso expiró o el navegador no guardó la cookie. Pedí un link nuevo.',
    cameraCaption: 'cámara',
    doorToast: 'Acceso expirado. Pedí un link nuevo.',
    copySeed: 'copiar',
    seedHint: 'compartí la semilla, no el link',
    seedCopied: 'copiado',
    connectFailed: function (code) {
      return 'No se pudo conectar al stream' + (code ? ' (error ' + code + ')' : '') +
        '.<br>La conexión se cortó y no volvió. Revisá la red.';
    },
    reconnectingLine: function (n, max) {
      return 'Stream cortado. Reconectando (' + n + '/' + max + ')...';
    },
    debugWebRtc: function (ms, t) {
      return 'transporte: webrtc  |  buffer de jitter: ' +
        (ms === null ? '?' : ms.toFixed(0) + ' ms') +
        '  |  t: ' + t.toFixed(1) + 's';
    },
    debugNative: 'transporte: hls nativo',
    debugHls: function (latency, target, rate) {
      return 'transporte: hls.js  |  latencia: ' + latency.toFixed(2) + 's' +
        '  |  objetivo: ' + (target === null ? '?' : target.toFixed(2)) + 's' +
        '  |  tasa: ' + rate.toFixed(2) + 'x';
    }
  };

  tel.textContent = location.host;

  /*
    The server's exchange RE-APPENDS ?t= on purpose: it is the fallback for
    browsers that do not keep cookies (Safari ITP, etc.). This page reads it
    once and takes it out of the address bar as soon as it can (F-01): first
    it asks whether the cookie opens —a request WITHOUT the suffix— and only
    a 404 keeps the token travelling on requests. It lives in memory, never
    in the address. See pedidoDeEstado().

    Exception: the camera carries `t` in the PATH of its URLs (relative HLS
    segments do not inherit the query — see caddy/Caddyfile). That is another
    channel, not the address bar, and it is not this slice.
  */
  var t = new URLSearchParams(location.search).get('t');
  var sufijo = t ? '?t=' + encodeURIComponent(t) : '';

  // One place for the status poll's options: the credential probe
  // (pedidoDeEstado) and the retry (alFallar) must ask with the same rules,
  // or the decision is taken with a different yardstick.
  // alFallar adds the bound's signal ON A COPY: this object is shared, and a
  // signal left on it (aborted or not) would cut the next request.
  var OPCIONES_ESTADO = { cache: 'no-store', credentials: 'include' };

  /*
    Takes ?t= out of the address and leaves the rest of the query intact:
    wiping the whole URL would also wipe ?debug=1, the only diagnostic knob.
    Idempotent: no t, nothing to do.
  */
  function limpiarBarra() {
    var q = new URLSearchParams(location.search);
    if (!q.has('t')) return;
    q.delete('t');
    var resto = q.toString();
    history.replaceState(null, '', location.pathname + (resto ? '?' + resto : ''));
  }

  // After the split the backend (Icecast/Caddy) lives on another origin: the
  // frontend is on Cloudflare Pages, the backend on the tunnel. On localhost
  // the backend is this same origin (no split); from anywhere else it is the
  // tunnel, with nothing to configure.
  var BACKEND_URL = 'https://stream.daza.ar';
  if (location.hostname === 'localhost' || location.hostname === '127.0.0.1') {
    BACKEND_URL = location.origin;
  }

