#!/usr/bin/env bash
# inject-backend-origin.sh — writes the deploy-time backend origin into
# web/js/00-backend.js for split deployments (page on one origin, stream on
# another). The door path never calls this; same-origin deployments leave the
# committed shim empty and default to location.origin.
#
# Usage:
#   ./scripts/inject-backend-origin.sh <origin>
# Example:
#   ./scripts/inject-backend-origin.sh https://stream.daza.ar

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="$ROOT/web/js/00-backend.js"

if [[ $# -ne 1 || -z "${1:-}" ]]; then
  echo "usage: $0 <origin>" >&2
  exit 1
fi

origin="$1"
tmp="$(mktemp "$TARGET.XXXXXX")"
printf "window.RADIO_BACKEND_URL = %s;\n" "$(printf '%s' "$origin" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))')" > "$tmp"
chmod 0644 "$tmp"
mv "$tmp" "$TARGET"

echo "Injected RADIO_BACKEND_URL into $TARGET"
