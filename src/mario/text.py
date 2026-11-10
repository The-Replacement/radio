"""Build the announcement text for a Host segment.

Deterministic: same inputs -> identical text. The text template version is a
constant (TEMPLATE_VERSION) so bumping it invalidates all cached segments.
"""

from __future__ import annotations

from . import TEMPLATE_VERSION
from .tracks import TrackMeta

THOUGHTS: tuple[str, ...] = (
    "The seed remembers what the ear forgets.",
    "Another lap around the same number.",
    "Nothing here is an accident, only a decision made earlier.",
    "The machine keeps its own time.",
    "Listen twice; it will not change.",
    "A small room, a single voice, a number that keeps returning.",
    "The door stays closed; the sound does not.",
    "We do not broadcast. We continue.",
)


def _thought(seed: int) -> str:
    return THOUGHTS[seed % len(THOUGHTS)]


def build_text(current: TrackMeta, nxt: TrackMeta, bucket: str, template_version: str = TEMPLATE_VERSION) -> str:
    """Return the announcement text. Deterministic in (current, nxt, bucket, version)."""
    # template_version is part of the contract but does not alter text beyond identity.
    _ = template_version
    hour = bucket[-2:] if "T" in bucket else "00"
    try:
        hour_int = int(hour)
    except ValueError:
        hour_int = 0
    time_phrase = f"It is hour {hour_int} of the day." if hour_int else "The clock keeps its own counsel."
    return (
        f"You just heard {current.lane}, {current.form}. "
        f"Next up: {nxt.lane}, {nxt.form}. "
        f"{time_phrase} "
        f"{_thought(current.seed + nxt.seed)}"
    )
