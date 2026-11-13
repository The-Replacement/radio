#!/usr/bin/env bash
# Backend access control without a credential.

smoke_group_no_credential() {
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
}
