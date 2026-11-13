#!/usr/bin/env bash
# Common helpers and state for smoke.sh. Sourced, not executed.

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

smoke_init() {
  echo
  echo "Smoke test — $BASE"
  echo

  # FRONTEND comes from .env (FRONTEND_URL): the frontend's origin.
  FRONTEND="${FRONTEND:-$(grep -E '^FRONTEND_URL=' "$RAIZ/.env" 2>/dev/null | cut -d= -f2- || true)}"
  FRONTEND="${FRONTEND:-http://localhost:8090}"
  FRONTEND="${FRONTEND%/}"

  # shellcheck disable=SC2034  # used by sourced probe scripts
  TOKEN="$(awk 'NF>=2 {print $2; exit}' "$RAIZ/secrets/tokens.txt" 2>/dev/null || true)"
  # shellcheck disable=SC2034  # used by sourced probe scripts
  COOKIE="$(awk 'NF>=3 {print $3; exit}' "$RAIZ/secrets/tokens.txt" 2>/dev/null || true)"
  # shellcheck disable=SC2034  # used by sourced probe scripts
  COOKIE_ENTREGADA=""
}

smoke_summary() {
  echo
  if [[ "$mal" -eq 0 ]]; then
    printf '\033[32mAll good\033[0m — %d checks passed.\n\n' "$ok"
    return 0
  else
    printf '\033[31m%d failures\033[0m out of %d checks.\n' "$mal" "$((ok+mal))"
    echo "Suggestions: docker compose logs -f caddy        (did it reload?)"
    echo "             docker compose logs -f liquidsoap   (does it connect to Icecast?)"
    echo
    return 1
  fi
}
