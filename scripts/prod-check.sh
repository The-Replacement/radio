#!/usr/bin/env bash
# prod-check.sh - explicit production/config checks for radio deployment.
#
# Focus: cookie/transport safety (#89) and sensitive credential handling notes (#87).
# Exits non-zero on unsafe production profiles; prints actionable guidance.
set -euo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$RAIZ/.env"

ok=0; mal=0; warn=0
pasa() { printf '  \033[32mOK\033[0m   %s\n' "$1"; ok=$((ok+1)); }
falla() { printf '  \033[31mFAIL\033[0m %s\n' "$1"; mal=$((mal+1)); }
aviso() { printf '  \033[33mWARN\033[0m %s\n' "$1"; warn=$((warn+1)); }

get_env() {
  local key="$1"
  grep -E "^${key}=" "$ENV_FILE" 2>/dev/null | cut -d= -f2- || true
}

echo
echo "Production/config checks - $RAIZ"
echo

if [[ ! -f "$ENV_FILE" ]]; then
  aviso ".env not found (using defaults in some contexts). Create it from .env.example for production."
fi

SITE_ADDRESS="$(get_env SITE_ADDRESS)"
PUBLIC_URL="$(get_env PUBLIC_URL)"
FRONTEND_URL="$(get_env FRONTEND_URL)"
COOKIE_FLAGS="$(get_env COOKIE_FLAGS)"

# #89: Secure cookie required when HTTPS is in use
if [[ "$SITE_ADDRESS" == https://* || "$PUBLIC_URL" == https://* ]]; then
  if [[ "$COOKIE_FLAGS" == *Secure* ]]; then
    pasa "COOKIE_FLAGS includes Secure for HTTPS configuration"
  else
    falla "HTTPS configured (SITE_ADDRESS/PUBLIC_URL) but COOKIE_FLAGS has no 'Secure' (add '; Secure')"
  fi
else
  pasa "non-HTTPS/local profile detected for cookie transport check"
fi

# Basic transport consistency hints
if [[ "$PUBLIC_URL" == https://* && "$SITE_ADDRESS" == http://* ]]; then
  aviso "PUBLIC_URL is https but SITE_ADDRESS is http - verify reverse proxy/TLS termination is intentional"
fi
if [[ -n "$FRONTEND_URL" && "$FRONTEND_URL" == http://* && "$PUBLIC_URL" == https://* ]]; then
  aviso "FRONTEND_URL is http while PUBLIC_URL is https - verify split-origin/CORS/cookie behavior"
fi

# #87: credential storage/lifecycle notes (explicit checks where possible)
TOKENS_FILE="$RAIZ/secrets/tokens.txt"
if [[ -f "$TOKENS_FILE" ]]; then
  perms="$(stat -c '%a' "$TOKENS_FILE" 2>/dev/null || echo '?')"
  if [[ "$perms" == "600" || "$perms" == "400" ]]; then
    pasa "secrets/tokens.txt permissions are restrictive ($perms)"
  else
    falla "secrets/tokens.txt permissions are $perms (expected 600); fix with: chmod 600 secrets/tokens.txt"
  fi
  if [[ -s "$TOKENS_FILE" ]]; then
    # warn if any credential has no expiry (4th field missing)
    if awk 'NF==3 {found=1} END{exit found?0:1}' "$TOKENS_FILE"; then
      aviso "one or more credentials have no expiry; prefer time-limited credentials and rotation"
    else
      pasa "all listed credentials include an expiry field"
    fi
  fi
  aviso "secrets/tokens.txt stores bearer credentials in plaintext - restrict host access/backups and avoid leaking logs/shell history"
else
  aviso "secrets/tokens.txt not found (no issued credentials yet)"
fi

echo
if [[ "$mal" -eq 0 ]]; then
  printf '\033[32mAll good\033[0m - %d ok, %d warning(s).\n\n' "$ok" "$warn"
  exit 0
else
  printf '\033[31m%d check(s) failed\033[0m - %d ok, %d warning(s).\n\n' "$mal" "$ok" "$warn"
  exit 1
fi
