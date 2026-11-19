#!/usr/bin/env bash
# Standalone entry point for the self-heal ladder (#106).
# Delegates to `radio-job heal` so there is one implementation.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec "$ROOT/scripts/radio-job.sh" heal "$@"