#!/bin/sh
# Triggers an immediate cycle of the generator (useful as a trigger in
# post-receive or by hand). The indiegen container watches
# /var/lib/indiegen/trigger.
set -eu
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TRIGGER="$ROOT/data/indiegen/trigger"
mkdir -p "$(dirname "$TRIGGER")"
date -u +%Y-%m-%dT%H:%M:%SZ > "$TRIGGER"
echo "trigger written: $TRIGGER"