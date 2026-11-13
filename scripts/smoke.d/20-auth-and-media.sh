#!/usr/bin/env bash
# Token exchange, authenticated access, stream bytes, running config, camera, and Mario host.

smoke_group_auth_and_media() {
  if [[ -z "$TOKEN" ]]; then
    falla "no credentials in secrets/tokens.txt (run: scripts/token.sh add <you>)"
    return
  fi

  echo
  echo "the link exchanged for a cookie:"
  # tr -d '\r': curl uses CRLF and the \r breaks the end-of-line anchors.
  local CABECERAS
  CABECERAS="$(curl -s -i --max-time 10 "$BASE/?t=$TOKEN" 2>/dev/null | tr -d '\r' || true)"
  grep -qiE '^set-cookie: radio=' <<<"$CABECERAS" \
    && pasa "the invitation link sets the cookie" \
    || falla "the invitation link did NOT set the cookie"
  grep -qE '^HTTP/[0-9.]+ 302' <<<"$CABECERAS" \
    && pasa "and redirects to the player (302)" \
    || falla "it did not redirect with 302"
  grep -qiF "location: $FRONTEND/" <<<"$CABECERAS" \
    && pasa "the redirect points at the frontend" \
    || falla "the redirect does not point at the frontend"
  grep -qiF "location: $FRONTEND/?t=$TOKEN" <<<"$CABECERAS" \
    && pasa "and keeps the ?t= in the URL (the no-cookie browser's fallback)" \
    || falla "the redirect lost the ?t=: the no-cookie browser loses the fallback"

  COOKIE_ENTREGADA="$(sed -nE 's/^[Ss]et-[Cc]ookie: radio=([^;]*).*/\1/p' <<<"$CABECERAS" | head -n1)"

  if [[ -z "$COOKIE_ENTREGADA" ]]; then
    falla "could not read the cookie the exchange handed out"
  else
    [[ "$(codigo -H "Cookie: radio=$COOKIE_ENTREGADA" "$BASE/")" == "200" ]] \
      && pasa "the cookie the exchange HANDS OUT serves the player" \
      || falla "the cookie the exchange hands out does not work: does the exchange copy the token instead of translating it? (token->cookieid map in caddy/tokens.caddy)"
    [[ "$(codigo_stream -H "Cookie: radio=$COOKIE_ENTREGADA" "$BASE/stream")" == "200" ]] \
      && pasa "the cookie the exchange HANDS OUT serves the stream" \
      || falla "the exchange's cookie did not authenticate the stream"
  fi

  # An invalid token must not overwrite a valid session with an empty cookie.
  if [[ -n "$COOKIE" ]]; then
    [[ "$(codigo -b /dev/null -H "Cookie: radio=$COOKIE" "$BASE/?t=basura")" == "200" ]] \
      && pasa "an invalid token does not break a valid session" \
      || falla "an invalid token overwrote the valid cookie"
  fi

  echo
  echo "with a valid credential:"
  [[ "$(codigo "$BASE/?t=$TOKEN")" == "302" ]] \
    && pasa "token -> exchange" || falla "the token did not exchange"

  if [[ -n "$COOKIE" ]]; then
    [[ "$(codigo_stream -H "Cookie: radio=$COOKIE" "$BASE/stream")" == "200" ]] \
      && pasa "cookie -> stream 200" || falla "the cookie did not authenticate the stream"
    [[ "$(codigo -H "Cookie: radio=$COOKIE" "$BASE/")" == "200" ]] \
      && pasa "cookie -> player 200" || falla "the cookie did not serve the player"
    [[ "$(codigo -H "Cookie: radio=$COOKIE" "$BASE/status-json.xsl")" == "200" ]] \
      && pasa "cookie -> status 200" || falla "the cookie did not serve the status"
  else
    falla "no cookieid: run scripts/token.sh ls and regenerate with add/rm"
  fi

  # The stream has to deliver real audio, not just a 200.
  local len
  len=$(curl -s --max-time 8 "$BASE/stream?t=$TOKEN" | head -c 65536 | wc -c)
  if [[ "${len:-0}" -gt 4096 ]]; then
    pasa "the stream delivers ${len} bytes of audio"
  else
    falla "the stream delivered ${len} bytes (is Liquidsoap connected? is there music in ./music?)"
  fi

  _smoke_running_rewrites
  _smoke_camera
}

_smoke_running_rewrites() {
  if docker compose -f "$RAIZ/docker-compose.yml" ps --status running --services 2>/dev/null | grep -qx caddy; then
    local cfg
    cfg="$(docker compose -f "$RAIZ/docker-compose.yml" exec -T caddy \
      wget -qO- http://127.0.0.1:2019/config/ 2>/dev/null || true)"
    if [[ -z "$cfg" ]]; then
      falla "could not read Caddy's running config (admin API on 2019)"
    else
      local rw
      for rw in '/stream?' '/status-json.xsl?' '/cam/whep?'; do
        grep -qF "\"rewrite\":{\"uri\":\"$rw\"}" <<<"$cfg" \
          && pasa "the running config has 'rewrite $rw'" \
          || falla "the running config does NOT have 'rewrite $rw' (did the reload not take?)"
      done
    fi
  fi
}

_smoke_camera() {
  # Optional: if there are no segments, there is no camera to test.
  if [[ ! -f "$RAIZ/data/cam/index.m3u8" ]]; then
    return
  fi

  echo
  echo "camera (HLS):"

  [[ "$(codigo "$BASE/cam/index.m3u8")" == "404" ]] \
    && pasa "playlist without a credential -> 404" \
    || falla "playlist without a credential -> $(codigo "$BASE/cam/index.m3u8")"
  [[ "$(codigo "$BASE/cam/init.mp4")" == "404" ]] \
    && pasa "init without a credential -> 404" \
    || falla "init without a credential -> $(codigo "$BASE/cam/init.mp4")"

  local seg="" tipo="" c=""
  if [[ -n "$COOKIE_ENTREGADA" ]]; then
    [[ "$(codigo -H "Cookie: radio=$COOKIE_ENTREGADA" "$BASE/cam/index.m3u8")" == "200" ]] \
      && pasa "playlist with cookie -> 200" \
      || falla "playlist with cookie -> $(codigo -H "Cookie: radio=$COOKIE_ENTREGADA" "$BASE/cam/index.m3u8")"

    tipo="$(curl -s -o /dev/null -D - --max-time 5 -H "Cookie: radio=$COOKIE_ENTREGADA" \
            "$BASE/cam/init.mp4" 2>/dev/null | tr -d '\r' | awk 'tolower($1)=="content-type:" {print $2}')"
    [[ "$tipo" == "video/mp4" ]] \
      && pasa "init with cookie -> 200 ($tipo)" \
      || falla "init with cookie served '$tipo' (expected video/mp4)"

    seg="$(grep -v '^#' "$RAIZ/data/cam/index.m3u8" | grep -m1 . || true)"
    if [[ -n "$seg" ]]; then
      c="$(codigo -H "Cookie: radio=$COOKIE_ENTREGADA" "$BASE/cam/$seg")"
      if [[ "$c" != "200" ]]; then
        seg="$(grep -v '^#' "$RAIZ/data/cam/index.m3u8" | grep -m1 . || true)"
        c="$(codigo -H "Cookie: radio=$COOKIE_ENTREGADA" "$BASE/cam/$seg")"
      fi
      [[ "$c" == "200" ]] && pasa "segment with cookie -> 200 ($seg)" \
                         || falla "segment with cookie -> $c ($seg)"
    fi

    tipo="$(curl -s -o /dev/null -D - --max-time 5 -H "Cookie: radio=$COOKIE_ENTREGADA" \
            "$BASE/cam/${seg:-nada}" 2>/dev/null | tr -d '\r' | awk 'tolower($1)=="content-type:" {print $2}')"
    [[ "$tipo" == "video/iso.segment" ]] \
      && pasa "the segment comes out as $tipo" \
      || falla "the segment came out as '$tipo' (expected video/iso.segment; did you switch to .ts?)"
  fi

  # --- WebRTC: the gate applies to the signalling ---
  [[ "$(codigo -X POST --data 'v=0' -H 'Content-Type: application/sdp' "$BASE/cam/whep")" == "404" ]] \
    && pasa "whep without a credential -> 404" \
    || falla "whep without a credential passed the gate"
  if [[ -n "$COOKIE_ENTREGADA" ]]; then
    [[ "$(codigo -X POST --data 'v=0' -H 'Content-Type: application/sdp' \
          -H "Cookie: radio=$COOKIE_ENTREGADA" "$BASE/cam/whep?t=$TOKEN")" == "400" ]] \
      && pasa "whep with a credential reaches mediamtx (400 = invalid SDP)" \
      || falla "whep with a credential did not reach mediamtx"
  fi

  # --- the token in the PATH, which is how players see it ---
  echo
  echo "  token in the path (VLC, mpv, ffmpeg):"

  [[ "$(codigo "$BASE/cam/$TOKEN/index.m3u8")" == "200" ]] \
    && pasa "playlist with token in the path -> 200" \
    || falla "playlist with token in the path -> $(codigo "$BASE/cam/$TOKEN/index.m3u8")"
  [[ "$(codigo "$BASE/cam/token-inventado/index.m3u8")" == "404" ]] \
    && pasa "invented token in the path -> 404" \
    || falla "the invented token passed the gate"
  [[ "$(codigo "$BASE/cam/$TOKEN/..%2fweb%2findex.html")" == "404" ]] \
    && pasa "cannot escape the directory -> 404" \
    || falla "the path with .. was not rejected"

  if [[ -n "${seg:-}" ]]; then
    [[ "$(codigo "$BASE/cam/$TOKEN/$seg")" == "200" ]] \
      && pasa "the relative segment inherits the token -> 200 ($seg)" \
      || falla "the relative segment did not inherit the token ($seg)"
  fi

  # The test that matters: a real player downloading everything.
  if command -v ffmpeg >/dev/null 2>&1; then
    if timeout 25 ffmpeg -hide_banner -v error -i "$BASE/cam/$TOKEN/index.m3u8" \
         -t 4 -f null - >/dev/null 2>&1; then
      pasa "ffmpeg downloads and decodes the camera with the token"
    else
      falla "ffmpeg could NOT download the camera with the token"
    fi
  fi
}

_smoke_mario() {
  # Optional: if Mario has not rendered any segment yet, there is nothing to test.
  if ! ls "$RAIZ"/data/host/*.wav >/dev/null 2>&1; then
    echo
    echo "host (Mario): SKIP — no cached segment in data/host/*.wav"
    return
  fi

  echo
  echo "host (Mario):"
  local seg name c tipo
  seg="$(ls "$RAIZ"/data/host/*.wav | head -n1)"
  name="$(basename "$seg")"

  [[ "$(codigo "$BASE/host/$name")" == "404" ]] \
    && pasa "segment without a credential -> 404" \
    || falla "segment without a credential -> $(codigo "$BASE/host/$name")"

  if [[ -n "$COOKIE_ENTREGADA" ]]; then
    c="$(codigo -H "Cookie: radio=$COOKIE_ENTREGADA" "$BASE/host/$name")"
    [[ "$c" == "200" ]] && pasa "segment with cookie -> 200 ($name)" \
                      || falla "segment with cookie -> $c ($name)"

    tipo="$(curl -s -o /dev/null -D - --max-time 5 -H "Cookie: radio=$COOKIE_ENTREGADA" \
            "$BASE/host/$name" 2>/dev/null | tr -d '\r' | awk 'tolower($1)=="content-type:" {print $2}')"
    [[ "$tipo" == audio/* ]] && pasa "segment content-type is audio/* ($tipo)" \
                            || falla "segment content-type '$tipo' (expected audio/*)"
  fi
}
