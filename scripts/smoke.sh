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
#
# Split into sourced helpers/probes for maintainability (S1/#77) while preserving
# this entrypoint, check semantics, and exit behavior.

SMOKE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# shellcheck source=scripts/smoke-lib.sh disable=SC1091
source "$SMOKE_DIR/smoke-lib.sh"
# shellcheck source=scripts/smoke.d/00-frontend-door.sh disable=SC1091
source "$SMOKE_DIR/smoke.d/00-frontend-door.sh"
# shellcheck source=scripts/smoke.d/10-no-credential.sh disable=SC1091
source "$SMOKE_DIR/smoke.d/10-no-credential.sh"
# shellcheck source=scripts/smoke.d/20-auth-and-media.sh disable=SC1091
source "$SMOKE_DIR/smoke.d/20-auth-and-media.sh"

smoke_init
smoke_group_frontend_door
smoke_group_no_credential
smoke_group_auth_and_media
_smoke_mario
smoke_summary
exit $?
