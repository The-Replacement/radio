#!/usr/bin/env bash
# smoke.sh — checks that the radio is up AND that the door closes properly.
#
# The important thing this script proves: that WITHOUT a credential nothing is
# visible, and that the token-for-cookie exchange works.
# A "private" radio that is visible without a credential is not private.
#
# There are TWO doors and this script measures both: the backend's (BASE) and
# the public page's (FRONTEND, the frontend origin). The second one is open, and
# measuring an open door is not the same as not measuring it: see group 0 (F-02).

set -uo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BASE="${BASE:-http://localhost:8090}"

ok=0; mal=0
pasa() { printf '  \033[32mOK\033[0m %s\n' "$1"; ok=$((ok+1)); }
falla() { printf '  \033[31mFAIL\033[0m %s\n' "$1"; mal=$((mal+1)); }
codigo() { curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$@" 2>/dev/null || echo 000; }

# For the stream, `codigo` does NOT work: the stream never ends, so curl keeps
# reading until the timeout and never prints the code.
# We save the headers to a file (they arrive right away) and read the first
# line. We do not use `| head -n1` on purpose: the SIGPIPE would make curl exit
# with an error and, with `set -o pipefail`, would contaminate the result.
codigo_stream() {
  local f
  f="$(mktemp)"
  curl -s -o /dev/null -D "$f" --max-time 3 "$@" 2>/dev/null || true
  awk 'NR==1 {print $2}' "$f"
  rm -f "$f"
}

echo
echo "Smoke test — $BASE"
echo

# ----------------------------------------------- the second door (group 0)
#
# The backend closes (the group below); the PAGE does not. Since the split the
# player lives on Cloudflare Pages and this repo has no Pages, no wrangler, no
# Access and no CI gating it: nothing stops the whole page from being served
# without a credential (F-02, issue #2, slice S5). It is measured anyway, even
# though it hurts, because a private radio whose banner is readable without a
# credential is also a problem — and a door nobody looks at is not a door.
#
# FRONTEND comes from .env (FRONTEND_URL): the frontend's origin. Locally it is
# http://localhost:8090. Fallback: .env does not exist on this machine.
# Measured on 2026-11-09: 200, 32252 bytes, byte-for-byte identical to
# web/index.html at the time of the measurement. stream.daza.ar, on the other
# hand, 404 on every path.
#
# DOCUMENTED EXCEPTION: the line below is the only thing authorizing a 200.
# Delete it and this gate goes back to demanding 404 — with the page open it
# goes red, which is the truth. If one day the page closes (404/403), delete the
# exception: a door that closed leaves no written authorization behind.
# With no network the code is 000 and this goes red on purpose: it could not be
# measured, and a check that did not measure does not pass in green (the F-07
# lesson).
# FRONTEND_URL is read from .env and, if it is not there, falls back to localhost.
# The only thing allowed to be hardcoded is the documented exception below; all
# the rest of the smoke measures with the environment's origin (S8, F-06 / F-13).
FRONTEND="${FRONTEND:-$(grep -E '^FRONTEND_URL=' "$RAIZ/.env" 2>/dev/null | cut -d= -f2- || true)}"
FRONTEND="${FRONTEND:-http://localhost:8090}"
FRONTEND="${FRONTEND%/}"
EXCEPCION_F02=200   # F-02: the page is served with no door (measured 2026-11-09)

echo "the second door (the page, no credential):"
ff="$(codigo "$FRONTEND/")"
if [[ "$ff" == "404" || "$ff" == "403" ]]; then
  pasa "the page closes without a credential ($ff)"
elif [[ -n "${EXCEPCION_F02+x}" && "$ff" == "$EXCEPCION_F02" ]]; then
  pasa "the page is served $ff without a credential — open door, documented exception (F-02, #2)"
else
  falla "the page is served '$ff' without a credential and with no exception covering it (F-02): close the door or write the exception here"
fi

echo
echo "without a credential:"
[[ "$(codigo "$BASE/")" == "404" ]] \
  && pasa "the player -> 404 (it does not reveal that there is a radio)" \
  || falla "the player -> $(codigo "$BASE/") (expected 404)"
[[ "$(codigo_stream "$BASE/stream")" == "404" ]] \
  && pasa "the stream -> 404" || falla "the stream -> $(codigo_stream "$BASE/stream")"
[[ "$(codigo_stream -H 'Cookie: radio=basura' "$BASE/stream")" == "404" ]] \
  && pasa "invalid cookie -> 404" || falla "the invalid cookie did not give 404"
[[ "$(codigo_stream "$BASE/stream?t=basura")" == "404" ]] \
  && pasa "invalid token -> 404" || falla "the invalid token did not give 404"

TOKEN="$(awk 'NF>=2 {print $2; exit}' "$RAIZ/secrets/tokens.txt" 2>/dev/null || true)"
COOKIE="$(awk 'NF>=3 {print $3; exit}' "$RAIZ/secrets/tokens.txt" 2>/dev/null || true)"

if [[ -z "$TOKEN" ]]; then
  falla "no credentials in secrets/tokens.txt (run: scripts/token.sh add <you>)"
else
  echo
  echo "the link exchanged for a cookie:"
  # tr -d '\r': curl uses CRLF and the \r breaks the end-of-line anchors.
  CABECERAS="$(curl -s -i --max-time 10 "$BASE/?t=$TOKEN" 2>/dev/null | tr -d '\r' || true)"
  grep -qiE '^set-cookie: radio=' <<<"$CABECERAS" \
    && pasa "the invitation link sets the cookie" \
    || falla "the invitation link did NOT set the cookie"
  grep -qE '^HTTP/[0-9.]+ 302' <<<"$CABECERAS" \
    && pasa "and redirects to the player (302)" \
    || falla "it did not redirect with 302"
  grep -qiE '^location: https://radio\.daza\.ar' <<<"$CABECERAS" \
    && pasa "the redirect points at the frontend" \
    || falla "the redirect does not point at the frontend"
  # The 302 does NOT clean the URL: it keeps the ?t= on purpose, it is the
  # fallback for the browser that does not keep cookies. Taking it out of the
  # bar is the page's job on load (S3/F-01). If this line fails, that browser
  # loses the fallback and does not even notice.
  grep -qiF "location: $FRONTEND/?t=$TOKEN" <<<"$CABECERAS" \
    && pasa "and keeps the ?t= in the URL (the no-cookie browser's fallback)" \
    || falla "the redirect lost the ?t=: the no-cookie browser loses the fallback"

  # THE COMPLETE CHAIN, not the halves.
  #
  # This is the only thing that proves the exchange WORKS: taking the cookie the
  # server has just handed out and using it. Before, this only looked at whether
  # the header existed and, separately, whether the tokens.txt cookie worked; the
  # exchange could hand out any garbage and the tests passed anyway. It handed
  # over the token, the gate compared it against the cookieid column, and
  # everybody ended up in 404.
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
  len=$(curl -s --max-time 8 "$BASE/stream?t=$TOKEN" | head -c 65536 | wc -c)
  if [[ "${len:-0}" -gt 4096 ]]; then
    pasa "the stream delivers ${len} bytes of audio"
  else
    falla "the stream delivered ${len} bytes (is Liquidsoap connected? is there music in ./music?)"
  fi

  # ------------------------------------------- the query, on what is running
  #
  # This replaces the old grep ('stream?t=' in Icecast's logs), which could not
  # fail: Icecast cuts the query at the '?' before storing __uri
  # (Icecast-common/httpp.c) and the access log writes that field as-is
  # (Icecast-Server/src/logging.c) — a string its format cannot contain, green
  # or red without measuring anything (F-07).
  #
  # What CAN be measured: the config Caddy is serving RIGHT NOW, through its
  # admin API (localhost:2019, only inside the container; the compose does not
  # publish it). If someone removes the '?' from the Caddyfile and does not
  # reload —or reloads and the change does not take—, the running config is left
  # without the rewrite and this check goes red. Measured in both directions
  # against the real config: with S4 the three greps match, with the pre-S4
  # config all three fail.
  #
  # MediaMTX has no grep of its own: at logLevel: info (production) it does not
  # record any request line (measured, with and without a query), so a grep in
  # its logs would be the same hole as Icecast's. Its rewrite (/cam/whep?) is
  # covered by this same check. The behaviour test —the '?' does not cross,
  # by mutation— lives in scripts/query-gate.sh.
  if docker compose -f "$RAIZ/docker-compose.yml" ps --status running --services 2>/dev/null | grep -qx caddy; then
    cfg="$(docker compose -f "$RAIZ/docker-compose.yml" exec -T caddy \
      wget -qO- http://127.0.0.1:2019/config/ 2>/dev/null || true)"
    if [[ -z "$cfg" ]]; then
      falla "could not read Caddy's running config (admin API on 2019)"
    else
      for rw in '/stream?' '/status-json.xsl?' '/cam/whep?'; do
        grep -qF "\"rewrite\":{\"uri\":\"$rw\"}" <<<"$cfg" \
          && pasa "the running config has 'rewrite $rw'" \
          || falla "the running config does NOT have 'rewrite $rw' (did the reload not take?)"
      done
    fi
  fi

  # ---------------------------------------------------------- the camera
  # Optional: if there are no segments, there is no camera to test.
  #
  # Careful with what is tested here. The HLS playlist is a file that points to
  # other files: a URL with ?t=... serves the playlist but NOT the segments,
  # because players request the relative paths without the query. That is why
  # the camera is tested WITH A COOKIE. For the browser it is transparent (the
  # exchange already set the cookie); a standalone player like VLC, on the other
  # hand, cannot see the camera with the token alone.
  if [[ -f "$RAIZ/data/cam/index.m3u8" ]]; then
    echo
    echo "camera (HLS):"

    [[ "$(codigo "$BASE/cam/index.m3u8")" == "404" ]] \
      && pasa "playlist without a credential -> 404" \
      || falla "playlist without a credential -> $(codigo "$BASE/cam/index.m3u8")"
    [[ "$(codigo "$BASE/cam/init.mp4")" == "404" ]] \
      && pasa "init without a credential -> 404" \
      || falla "init without a credential -> $(codigo "$BASE/cam/init.mp4")"

    if [[ -n "$COOKIE_ENTREGADA" ]]; then
      [[ "$(codigo -H "Cookie: radio=$COOKIE_ENTREGADA" "$BASE/cam/index.m3u8")" == "200" ]] \
        && pasa "playlist with cookie -> 200" \
        || falla "playlist with cookie -> $(codigo -H "Cookie: radio=$COOKIE_ENTREGADA" "$BASE/cam/index.m3u8")"

      # init.mp4 and not a segment: segments rotate (delete_segments) and a name
      # read a second ago may no longer exist. 404s from rotation are noise, not
      # failures.
      tipo="$(curl -s -o /dev/null -D - --max-time 5 -H "Cookie: radio=$COOKIE_ENTREGADA" \
              "$BASE/cam/init.mp4" 2>/dev/null | tr -d '\r' | awk 'tolower($1)=="content-type:" {print $2}')"
      [[ "$tipo" == "video/mp4" ]] \
        && pasa "init with cookie -> 200 ($tipo)" \
        || falla "init with cookie served '$tipo' (expected video/mp4)"

      # A real segment, with one retry in case it rotated.
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
    # The SDP goes through Caddy and through the gate; the video does not (it
    # goes over direct UDP). Without a credential, signalling has to give 404
    # like everything else.
    [[ "$(codigo -X POST --data 'v=0' -H 'Content-Type: application/sdp' "$BASE/cam/whep")" == "404" ]] \
      && pasa "whep without a credential -> 404" \
      || falla "whep without a credential passed the gate"
    if [[ -n "$COOKIE_ENTREGADA" ]]; then
      # 400 = it reached mediamtx, which rejects the test SDP. 404 would mean
      # Caddy never routed it. It is sent WITH ?t=, which is the real shape of
      # the no-cookie fallback (F-07): the rewrite has to delete it without
      # breaking the path — if the rewrite pointed elsewhere, mediamtx would
      # answer 404 and this would go red.
      [[ "$(codigo -X POST --data 'v=0' -H 'Content-Type: application/sdp' \
            -H "Cookie: radio=$COOKIE_ENTREGADA" "$BASE/cam/whep?t=$TOKEN")" == "400" ]] \
        && pasa "whep with a credential reaches mediamtx (400 = invalid SDP)" \
        || falla "whep with a credential did not reach mediamtx"
    fi

    # --- the token in the PATH, which is how players see it ---
    # With ?t=... the playlist downloads and the segments give 404: relative
    # requests do not carry the query. This tests the path that does work.
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
  fi
fi


# ---------------------------------------------------------- Mario (the host voice)
# Optional: if Mario has not rendered any segment yet, there is nothing to test.
# Mirrors the camera group: SKIP (no fail) when no cached wav exists.
if ls "$RAIZ"/data/host/*.wav >/dev/null 2>&1; then
  echo
  echo "host (Mario):"
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
else
  echo
  echo "host (Mario): SKIP — no cached segment in data/host/*.wav"
fi

echo
if [[ "$mal" -eq 0 ]]; then
  printf '\033[32mAll good\033[0m — %d checks passed.\n\n' "$ok"
  exit 0
else
  printf '\033[31m%d failures\033[0m out of %d checks.\n' "$mal" "$((ok+mal))"
  echo "Suggestions: docker compose logs -f caddy        (did it reload?)"
  echo "             docker compose logs -f liquidsoap   (does it connect to Icecast?)"
  echo
  exit 1
fi
