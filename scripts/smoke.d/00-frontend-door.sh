#!/usr/bin/env bash
# Group 0: the second door (the public/frontend page, no credential).

smoke_group_frontend_door() {
  # DOCUMENTED EXCEPTION: the line below is the only thing authorizing a 200.
  # Delete it and this gate goes back to demanding 404 — with the page open it
  # goes red, which is the truth. If one day the page closes (404/403), delete the
  # exception: a door that closed leaves no written authorization behind.
  local EXCEPCION_F02=200   # F-02: the page is served with no door (measured 2026-11-09)

  echo "the second door (the page, no credential):"
  local ff
  ff="$(codigo "$FRONTEND/")"
  if [[ "$ff" == "404" || "$ff" == "403" ]]; then
    pasa "the page closes without a credential ($ff)"
  elif [[ -n "${EXCEPCION_F02+x}" && "$ff" == "$EXCEPCION_F02" ]]; then
    pasa "the page is served $ff without a credential — open door, documented exception (F-02, #2)"
  else
    falla "the page is served '$ff' without a credential and with no exception covering it (F-02): close the door or write the exception here"
  fi
}
