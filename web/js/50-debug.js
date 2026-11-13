  /*
    ?debug=1 shows how far the player lags behind live. It is the only way to
    tell "the pipeline is slow" from "the player is waiting" without opening
    DevTools. If the number is ~2, the server is fine and the video is seen 2s
    late; if it is 10, the config was not applied.
  */
  if (new URLSearchParams(location.search).has('debug')) {
    var dbg = document.getElementById('debug');
    setInterval(function () {
      if (pcWebrtc) {
        // In WebRTC there is no "position in the playlist": what is measured
        // is the receiver's jitter buffer, which is almost the whole delay of
        // the hop.
        pcWebrtc.getStats().then(function (stats) {
          var ms = null;
          stats.forEach(function (r) {
            if (r.type === 'inbound-rtp' && r.kind === 'video' &&
                r.jitterBufferEmittedCount) {
              ms = (r.jitterBufferDelay / r.jitterBufferEmittedCount) * 1000;
            }
          });
          dbg.textContent = STR.debugWebRtc(ms, cam.currentTime);
        });
        return;
      }
      if (!hlsCam) { dbg.textContent = STR.debugNative; return; }
      dbg.textContent = STR.debugHls(hlsCam.latency, hlsCam.targetLatency, cam.playbackRate);
    }, 1000);
  }
