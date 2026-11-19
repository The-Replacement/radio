#!/usr/bin/env bash
# Group: Tor .onion door (audio-only, AUD-05/#26). Skipped if tor/onion unavailable.

smoke_group_tor() {
  echo "tor door (audio-only, optional):"
  local onion_file="$ROOT/data/tor/hidden_service/hostname"
  local onion="${TOR_ONION:-}"
  local socks="${TOR_SOCKS:-127.0.0.1:9050}"

  if [[ -z "$onion" && -f "$onion_file" ]]; then
    onion="$(tr -d '\r\n' < "$onion_file" 2>/dev/null || true)"
  fi

  if [[ -z "$onion" || -z "$TOKEN" ]]; then
    printf '  \033[33mSKIP\033[0m tor checks (no onion hostname or token)\n'
    return 0
  fi
  if ! command -v curl >/dev/null 2>&1; then
    printf '  \033[33mSKIP\033[0m tor checks (curl not found)\n'
    return 0
  fi

  # Audio-only gate: direct stream with token must return audio bytes, no clearnet redirect.
  local code
  code="$(curl --socks5-hostname "$socks" -s -o /dev/null -w '%{http_code}' --max-time 15 \
    "http://${onion}/stream?t=${TOKEN}" 2>/dev/null || echo 000)"
  if [[ "$code" == "200" ]]; then
    pass "tor /stream?t=TOKEN returns 200 (audio-only door)"
  else
    fail "tor /stream?t=TOKEN returned '$code' (expected 200)"
  fi

  # No clearnet redirect on canje over .onion.
  local headers
  headers="$(curl --socks5-hostname "$socks" -s -D - -o /dev/null --max-time 15 \
    "http://${onion}/?t=${TOKEN}" 2>/dev/null || true)"
  if grep -qi '^location: https://comechingona\.daza\.ar' <<<"$headers"; then
    fail "tor canje leaked clearnet frontend redirect"
  else
    pass "tor canje does not redirect to clearnet frontend"
  fi
}
