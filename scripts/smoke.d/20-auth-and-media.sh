#!/usr/bin/env bash
# Token exchange, authenticated access, stream bytes, running config, camera, and Mario host.

smoke_group_auth_and_media() {
  if [[ -z "$TOKEN" ]]; then
    fail "no credentials in secrets/tokens.txt (run: scripts/token.sh add <you>)"
    return
  fi

  echo
  echo "the link exchanged for a cookie:"
  # tr -d '\r': curl uses CRLF and the \r breaks the end-of-line anchors.
  local HEADERS
  HEADERS="$(curl -s -i --max-time 10 "$BASE/?t=$TOKEN" 2>/dev/null | tr -d '\r' || true)"
  grep -qiE '^set-cookie: radio=' <<<"$HEADERS" \
    && pass "the invitation link sets the cookie" \
    || fail "the invitation link did NOT set the cookie"
  grep -qE '^HTTP/[0-9.]+ 302' <<<"$HEADERS" \
    && pass "and redirects to the player (302)" \
    || fail "it did not redirect with 302"
  grep -qiF "location: $FRONTEND/" <<<"$HEADERS" \
    && pass "the redirect points at the frontend" \
    || fail "the redirect does not point at the frontend"
  grep -qiF "location: $FRONTEND/?t=$TOKEN" <<<"$HEADERS" \
    && pass "and keeps the ?t= in the URL (the no-cookie browser's fallback)" \
    || fail "the redirect lost the ?t=: the no-cookie browser loses the fallback"

  COOKIE_DELIVERED="$(sed -nE 's/^[Ss]et-[Cc]ookie: radio=([^;]*).*/\1/p' <<<"$HEADERS" | head -n1)"

  if [[ -z "$COOKIE_DELIVERED" ]]; then
    fail "could not read the cookie the exchange handed out"
  else
    [[ "$(status_code -H "Cookie: radio=$COOKIE_DELIVERED" "$BASE/")" == "200" ]] \
      && pass "the cookie the exchange HANDS OUT serves the player" \
      || fail "the cookie the exchange hands out does not work: does the exchange copy the token instead of translating it? (token->cookieid map in caddy/tokens.caddy)"
    [[ "$(status_code_stream -H "Cookie: radio=$COOKIE_DELIVERED" "$BASE/stream")" == "200" ]] \
      && pass "the cookie the exchange HANDS OUT serves the stream" \
      || fail "the exchange's cookie did not authenticate the stream"
  fi

  # An invalid token must not overwrite a valid session with an empty cookie.
  if [[ -n "$COOKIE" ]]; then
    [[ "$(status_code -b /dev/null -H "Cookie: radio=$COOKIE" "$BASE/?t=basura")" == "200" ]] \
      && pass "an invalid token does not break a valid session" \
      || fail "an invalid token overwrote the valid cookie"
  fi

  echo
  echo "with a valid credential:"
  [[ "$(status_code "$BASE/?t=$TOKEN")" == "302" ]] \
    && pass "token -> exchange" || fail "the token did not exchange"

  if [[ -n "$COOKIE" ]]; then
    [[ "$(status_code_stream -H "Cookie: radio=$COOKIE" "$BASE/stream")" == "200" ]] \
      && pass "cookie -> stream 200" || fail "the cookie did not authenticate the stream"
    [[ "$(status_code -H "Cookie: radio=$COOKIE" "$BASE/")" == "200" ]] \
      && pass "cookie -> player 200" || fail "the cookie did not serve the player"
    [[ "$(status_code -H "Cookie: radio=$COOKIE" "$BASE/status-json.xsl")" == "200" ]] \
      && pass "cookie -> status 200" || fail "the cookie did not serve the status"
  else
    fail "no cookieid: run scripts/token.sh ls and regenerate with add/rm"
  fi

  # The stream has to deliver real audio, not just a 200.
  local len
  len=$(curl -s --max-time 8 "$BASE/stream?t=$TOKEN" | head -c 65536 | wc -c)
  len="${len//[[:space:]]/}"
  if [[ "${len:-0}" -gt 4096 ]]; then
    pass "the stream delivers ${len} bytes of audio"
  else
    fail "the stream delivered ${len} bytes (is Liquidsoap connected? is there music in ./music?)"
  fi

  _smoke_running_rewrites
  _smoke_camera
}

_smoke_running_rewrites() {
  if docker compose -f "$ROOT/docker-compose.yml" ps --status running --services 2>/dev/null | grep -qx caddy; then
    local cfg
    cfg="$(docker compose -f "$ROOT/docker-compose.yml" exec -T caddy \
      wget -qO- http://127.0.0.1:2019/config/ 2>/dev/null || true)"
    if [[ -z "$cfg" ]]; then
      fail "could not read Caddy's running config (admin API on 2019)"
    else
      local rw
      for rw in '/stream?' '/status-json.xsl?' '/cam/whep?'; do
        grep -qF "\"rewrite\":{\"uri\":\"$rw\"}" <<<"$cfg" \
          && pass "the running config has 'rewrite $rw'" \
          || fail "the running config does NOT have 'rewrite $rw' (did the reload not take?)"
      done
    fi
  fi
}

_smoke_camera() {
  # Optional: if there are no segments, there is no camera to test.
  if [[ ! -f "$ROOT/data/cam/index.m3u8" ]]; then
    return
  fi

  echo
  echo "camera (HLS):"

  [[ "$(status_code "$BASE/cam/index.m3u8")" == "404" ]] \
    && pass "playlist without a credential -> 404" \
    || fail "playlist without a credential -> $(status_code "$BASE/cam/index.m3u8")"
  [[ "$(status_code "$BASE/cam/init.mp4")" == "404" ]] \
    && pass "init without a credential -> 404" \
    || fail "init without a credential -> $(status_code "$BASE/cam/init.mp4")"

  local segment="" content_type="" code=""
  if [[ -n "$COOKIE_DELIVERED" ]]; then
    [[ "$(status_code -H "Cookie: radio=$COOKIE_DELIVERED" "$BASE/cam/index.m3u8")" == "200" ]] \
      && pass "playlist with cookie -> 200" \
      || fail "playlist with cookie -> $(status_code -H "Cookie: radio=$COOKIE_DELIVERED" "$BASE/cam/index.m3u8")"

    content_type="$(curl -s -o /dev/null -D - --max-time 5 -H "Cookie: radio=$COOKIE_DELIVERED" \
            "$BASE/cam/init.mp4" 2>/dev/null | tr -d '\r' | awk 'tolower($1)=="content-type:" {print $2}')"
    [[ "$content_type" == "video/mp4" ]] \
      && pass "init with cookie -> 200 ($content_type)" \
      || fail "init with cookie served '$content_type' (expected video/mp4)"

    segment="$(grep -v '^#' "$ROOT/data/cam/index.m3u8" | grep -m1 . || true)"
    if [[ -n "$segment" ]]; then
      code="$(status_code -H "Cookie: radio=$COOKIE_DELIVERED" "$BASE/cam/$segment")"
      if [[ "$code" != "200" ]]; then
        segment="$(grep -v '^#' "$ROOT/data/cam/index.m3u8" | grep -m1 . || true)"
        code="$(status_code -H "Cookie: radio=$COOKIE_DELIVERED" "$BASE/cam/$segment")"
      fi
      [[ "$code" == "200" ]] && pass "segment with cookie -> 200 ($segment)" \
                         || fail "segment with cookie -> $code ($segment)"
    fi

    content_type="$(curl -s -o /dev/null -D - --max-time 5 -H "Cookie: radio=$COOKIE_DELIVERED" \
            "$BASE/cam/${segment:-none}" 2>/dev/null | tr -d '\r' | awk 'tolower($1)=="content-type:" {print $2}')"
    [[ "$content_type" == "video/iso.segment" ]] \
      && pass "the segment comes out as $content_type" \
      || fail "the segment came out as '$content_type' (expected video/iso.segment; did you switch to .ts?)"
  fi

  # --- WebRTC: the gate applies to the signalling ---
  [[ "$(status_code -X POST --data 'v=0' -H 'Content-Type: application/sdp' "$BASE/cam/whep")" == "404" ]] \
    && pass "whep without a credential -> 404" \
    || fail "whep without a credential passed the gate"
  if [[ -n "$COOKIE_DELIVERED" ]]; then
    [[ "$(status_code -X POST --data 'v=0' -H 'Content-Type: application/sdp' \
          -H "Cookie: radio=$COOKIE_DELIVERED" "$BASE/cam/whep?t=$TOKEN")" == "400" ]] \
      && pass "whep with a credential reaches mediamtx (400 = invalid SDP)" \
      || fail "whep with a credential did not reach mediamtx"
  fi

  # --- the token in the PATH, which is how players see it ---
  echo
  echo "  token in the path (VLC, mpv, ffmpeg):"

  [[ "$(status_code "$BASE/cam/$TOKEN/index.m3u8")" == "200" ]] \
    && pass "playlist with token in the path -> 200" \
    || fail "playlist with token in the path -> $(status_code "$BASE/cam/$TOKEN/index.m3u8")"
  [[ "$(status_code "$BASE/cam/token-inventado/index.m3u8")" == "404" ]] \
    && pass "invented token in the path -> 404" \
    || fail "the invented token passed the gate"
  [[ "$(status_code "$BASE/cam/$TOKEN/..%2fweb%2findex.html")" == "404" ]] \
    && pass "cannot escape the directory -> 404" \
    || fail "the path with .. was not rejected"

  if [[ -n "${segment:-}" ]]; then
    [[ "$(status_code "$BASE/cam/$TOKEN/$segment")" == "200" ]] \
      && pass "the relative segment inherits the token -> 200 ($segment)" \
      || fail "the relative segment did not inherit the token ($segment)"
  fi

  # The test that matters: a real player downloading everything.
  if command -v ffmpeg >/dev/null 2>&1; then
    if timeout 25 ffmpeg -hide_banner -v error -i "$BASE/cam/$TOKEN/index.m3u8" \
         -t 4 -f null - >/dev/null 2>&1; then
      pass "ffmpeg downloads and decodes the camera with the token"
    else
      fail "ffmpeg could NOT download the camera with the token"
    fi
  fi
}

_smoke_mario() {
  # Optional: if Mario has not rendered any segment yet, there is nothing to test.
  if ! ls "$ROOT"/data/host/*.wav >/dev/null 2>&1; then
    echo
    echo "host (Mario): SKIP — no cached segment in data/host/*.wav"
    return
  fi

  echo
  echo "host (Mario):"
  local segment name code content_type
  segment="$(ls "$ROOT"/data/host/*.wav | head -n1)"
  name="$(basename "$segment")"

  [[ "$(status_code "$BASE/host/$name")" == "404" ]] \
    && pass "segment without a credential -> 404" \
    || fail "segment without a credential -> $(status_code "$BASE/host/$name")"

  if [[ -n "$COOKIE_DELIVERED" ]]; then
    code="$(status_code -H "Cookie: radio=$COOKIE_DELIVERED" "$BASE/host/$name")"
    [[ "$code" == "200" ]] && pass "segment with cookie -> 200 ($name)" \
                      || fail "segment with cookie -> $code ($name)"

    content_type="$(curl -s -o /dev/null -D - --max-time 5 -H "Cookie: radio=$COOKIE_DELIVERED" \
            "$BASE/host/$name" 2>/dev/null | tr -d '\r' | awk 'tolower($1)=="content-type:" {print $2}')"
    [[ "$content_type" == audio/* ]] && pass "segment content-type is audio/* ($content_type)" \
                            || fail "segment content-type '$content_type' (expected audio/*)"
  fi
}
