#!/usr/bin/env bash
# Backend access control without a credential.

smoke_group_no_credential() {
  echo
  echo "without a credential:"
  [[ "$(status_code "$BASE/")" == "404" ]] \
    && pass "the player -> 404 (it does not reveal that there is a radio)" \
    || fail "the player -> $(status_code "$BASE/") (expected 404)"
  [[ "$(status_code_stream "$BASE/stream")" == "404" ]] \
    && pass "the stream -> 404" || fail "the stream -> $(status_code_stream "$BASE/stream")"
  [[ "$(status_code_stream -H 'Cookie: radio=basura' "$BASE/stream")" == "404" ]] \
    && pass "invalid cookie -> 404" || fail "the invalid cookie did not give 404"
  [[ "$(status_code_stream "$BASE/stream?t=basura")" == "404" ]] \
    && pass "invalid token -> 404" || fail "the invalid token did not give 404"
}
