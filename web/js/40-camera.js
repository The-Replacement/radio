  /*
    ------------------------------------------------------------------
    The camera. Muted video: the audio is the radio's and nothing else.
    ------------------------------------------------------------------

    The camera exists only if there is a fresh playlist NOW. The HLS index
    has carried EXT-X-PROGRAM-DATE-TIME on every segment from the start; it
    looks at which one is last and, if more than ~10 s have passed, the box
    does not even appear: the camera is blind, and waiting for it to come
    back is the same as not having it (F-08).

    Why before WebRTC: with no fresh playlist there is no SDP to negotiate
    and no 380 KB of hls.js to request. It asks before touching anything.
  */
  function hayCamara() {
    return fetch(BACKEND_URL + '/cam/index.m3u8' + sufijo,
                 { cache: 'no-store', credentials: 'include' })
      .then(function (r) {
        // 404 = no camera; nothing to show.
        if (!r.ok) return null;
        return r.text();
      })
      .catch(function () { return null; })
      .then(function (texto) {
        if (texto === null) return false;
        var m = texto.match(/^#EXT-X-PROGRAM-DATE-TIME:(.+)$/gm);
        if (!m || !m.length) return false;
        var ultimo = m[m.length - 1].split(':').slice(1).join(':').trim();
        var t = Date.parse(ultimo);
        if (!isFinite(t)) return false;
        return (Date.now() - t) < 10000;
      });
  }

  var caja = document.getElementById('cam-caja');
  var cam = document.getElementById('cam');
  var hlsCam = null;
  var pcWebrtc = null;

  function mostrar() { caja.hidden = false; if (camCaption) { camCaption.textContent = STR.cameraCaption; camCaption.hidden = false; } }

  function porWebrtc() {
    /*
      WebRTC first: it is the only browser transport that does not wait for
      a segment to close. The jitter buffer is tens of ms, not seconds.

      The signalling request (POST of the SDP) goes through Caddy and passes
      the gate like any other request. The video does NOT: it travels over
      UDP straight to mediamtx. If the UDP does not arrive there is no track,
      and it falls back to HLS.
    */
    var pc = new RTCPeerConnection();
    pcWebrtc = pc;
    pc.addTransceiver('video', { direction: 'recvonly' });

    var primerFrame = new Promise(function (listo, fallo) {
      pc.addEventListener('track', function (e) {
        cam.srcObject = e.streams[0] || new MediaStream([e.track]);
        cam.play().catch(function () {});
        mostrar();
        listo();
      });
      // No track, no video: a fallback beats a black rectangle.
      setTimeout(function () { fallo(new Error('sin track')); }, 6000);
    });

    return pc.createOffer()
      .then(function (of) { return pc.setLocalDescription(of); })
      .then(function () { return esperarIce(pc); })
      .then(function () {
        return fetch(BACKEND_URL + '/cam/whep' + sufijo, {
          method: 'POST',
          credentials: 'include',
          headers: { 'Content-Type': 'application/sdp' },
          body: pc.localDescription.sdp
        });
      })
      .then(function (r) {
        if (!r.ok) throw new Error('whep ' + r.status);
        return r.text();
      })
      .then(function (sdp) {
        return pc.setRemoteDescription({ type: 'answer', sdp: sdp });
      })
      .then(function () { return primerFrame; })
      .catch(function (e) {
        pc.close();
        pcWebrtc = null;
        throw e;
      });
  }

  // WHEP without trickle: the SDP must be sent with the candidates gathered.
  function esperarIce(pc) {
    return new Promise(function (listo) {
      if (pc.iceGatheringState === 'complete') return listo();
      var corte = setTimeout(listo, 2000);
      pc.addEventListener('icegatheringstatechange', function () {
        if (pc.iceGatheringState === 'complete') { clearTimeout(corte); listo(); }
      });
    });
  }

  function porHls() {
    /*
      Fallback. hls.js FIRST, even if the browser claims it plays native
      HLS: Chrome 154 answers "maybe" to canPlayType and its native
      implementation buffers ~10s with no knob. It cost two wrong diagnoses.
    */
    var s = document.createElement('script');
    s.src = '/vendor/hls.light.min.js';
    s.onload = function () {
      if (window.Hls && window.Hls.isSupported()) { hlsConLib(); return; }
      nativo();
    };
    s.onerror = nativo;
    document.head.appendChild(s);
  }

  function nativo() {
    if (!cam.canPlayType('application/vnd.apple.mpegurl')) return;
    cam.crossOrigin = 'use-credentials';
    cam.src = t
      ? BACKEND_URL + '/cam/' + encodeURIComponent(t) + '/index.m3u8'
      : BACKEND_URL + '/cam/index.m3u8';
    cam.addEventListener('loadedmetadata', mostrar);
    cam.play().catch(function () { /* autoplay blocked: doesn't matter, it's muted */ });
  }

  function hlsConLib() {
    /*
      liveSyncDuration is in SECONDS (hls.js: liveSyncPosition = liveEdge -
      targetLatency), so it does not depend on the segment size.

      Why 1.5 and not less: measured with headless Chrome against this
      stream, 1.0 gave 1.1s but with hiccups (readyState dropped to 2 now and
      then: it ran out of buffer at the edge). 1.5 gives a steady 1.56s, not a
      single hiccup in 30s. That is the floor of HLS with 1s segments.
    */
    var hls = new window.Hls({
      xhrSetup: function (xhr) { xhr.withCredentials = true; },
      fetchSetup: function (context, initParams) { return new Request(context.url, Object.assign({}, initParams, { credentials: 'include' })); },
      liveSyncDuration: 1.5,
      liveMaxLatencyDuration: 5,
      maxLiveSyncPlaybackRate: 1.5,
      backBufferLength: 15
    });
    hlsCam = hls;
    hls.loadSource(t
      ? BACKEND_URL + '/cam/' + encodeURIComponent(t) + '/index.m3u8'
      : BACKEND_URL + '/cam/index.m3u8');
    hls.attachMedia(cam);
    hls.on(window.Hls.Events.MANIFEST_PARSED, function () { mostrar(); });
    hls.on(window.Hls.Events.ERROR, function (nada, datos) {
      // Fatal = no way to go on. The box is hidden instead of leaving a
      // frozen black rectangle.
      if (datos && datos.fatal) { caja.hidden = true; if (camCaption) camCaption.hidden = true; hls.destroy(); }
    });
    cam.play().catch(function () {});
  }

  function arrancar() {
    hayCamara().then(function (ok) {
      if (!ok) { caja.hidden = true; if (camCaption) camCaption.hidden = true; return; }
      if (!window.RTCPeerConnection) { porHls(); return; }
      porWebrtc().catch(function () { porHls(); });
    });
  }

  arrancar();

