"""Build the DJ announcement text for a Host segment.

Deterministic: same inputs -> identical text. The text template version is a
constant (TEMPLATE_VERSION) so bumping it invalidates all cached segments.
"""

from __future__ import annotations

from . import TEMPLATE_VERSION
from .tracks import TrackMeta
from .vibe import vibe_for

# Openers: the DJ claims the act of steering/transitioning (outgoing lane context).
_OPENERS_PULSE: tuple[str, ...] = (
    "I'm holding this pulse a moment longer... then letting it go.",
    "I ride that motor out... and ease my hand off the wheel.",
    "I keep the drive steady... until I choose to turn.",
)
_OPENERS_DRIFT: tuple[str, ...] = (
    "I'm letting that drift dissolve... slow, on purpose.",
    "I hold the space open... and let it fall away.",
    "I loosen my grip on that haze... and let it breathe out.",
)
_OPENERS_HUSH: tuple[str, ...] = (
    "I'm keeping the hush close... then setting it down, gently.",
    "I let that quiet hover... before I move the air.",
    "I cradle the silence... and release it, softly.",
)

# Cues: claim the act of playing/dropping the incoming track (incoming vibe).
_CUES_PULSE: tuple[str, ...] = (
    "I'm sliding you into a steady current... {lane}, {form}. Let it take the wheel.",
    "I'm dropping {lane}, {form}... firm, hypnotic, unbroken.",
    "I'm locking the frequency to {lane}, {form}... let the motion carry you.",
)
_CUES_DRIFT: tuple[str, ...] = (
    "I'm steering you toward {lane}, {form}... wide, weightless, endless.",
    "I'm opening {lane}, {form}... let it drift you out past the edges.",
    "I'm setting {lane}, {form} in motion... slow orbit, open sky.",
)
_CUES_HUSH: tuple[str, ...] = (
    "I'm lowering you into {lane}, {form}... hush, and a long exhale.",
    "I'm placing {lane}, {form} in the room... weightless, barely there.",
    "I'm dimming the lights for {lane}, {form}... let the quiet hold you.",
)

# Clairvoyant/time images — subtle, atmospheric.
_VISIONS: tuple[str, ...] = (
    "I saw this turn before it arrived.",
    "The next shape was already waiting in the signal.",
    "I felt this frequency coming long before the fade.",
    "The room told me where to go... I just listened first.",
    "I knew the air would need this, right about now.",
)
_TIME_IMAGES: tuple[str, ...] = (
    "This hour hangs low... unhurried.",
    "The hour stretches, thin and silver.",
    "Time moves like water here... slow and certain.",
    "The clock hums softly in the dark.",
)


def _pick(options: tuple[str, ...], key: int) -> str:
    return options[key % len(options)]


def _openers_for(mood: str) -> tuple[str, ...]:
    if mood == "pulse":
        return _OPENERS_PULSE
    if mood == "hush":
        return _OPENERS_HUSH
    return _OPENERS_DRIFT


def _cues_for(mood: str) -> tuple[str, ...]:
    if mood == "pulse":
        return _CUES_PULSE
    if mood == "hush":
        return _CUES_HUSH
    return _CUES_DRIFT


def _bucket_hour(bucket: str) -> int | None:
    if "T" not in bucket:
        return None
    hour = bucket.split("T", 1)[1][:2]
    try:
        return int(hour)
    except ValueError:
        return None


def build_text(
    current: TrackMeta, nxt: TrackMeta, bucket: str, template_version: str = TEMPLATE_VERSION
) -> str:
    """Return the DJ announcement text. Deterministic in (current, nxt, bucket, version)."""
    # template_version is part of the contract/identity (cache invalidation signal).
    _ = template_version
    vibe = vibe_for(current, nxt, bucket)
    mix = current.seed * 131542391 + nxt.seed * 2654435761 + sum(ord(c) for c in bucket)

    opener = _pick(_openers_for(vibe.current_mood), mix)
    cue = _pick(_cues_for(vibe.next_mood), mix >> 3).format(lane=nxt.lane, form=nxt.form)

    parts = [opener]
    # If the character shifts, name the crossing softly.
    if vibe.current_mood != vibe.next_mood:
        shifts = {
            ("pulse", "drift"): "I slow the room down... let the edges blur.",
            ("pulse", "hush"): "I dim the motion... let everything settle.",
            ("drift", "pulse"): "I gather the drift... and give it a spine.",
            ("drift", "hush"): "I fold the space inward... into stillness.",
            ("hush", "pulse"): "I lift the quiet... and set a heartbeat under it.",
            ("hush", "drift"): "I open the silence... and let it wander.",
        }
        parts.append(shifts.get((vibe.current_mood, vibe.next_mood), "I turn the room, gently."))
    parts.append(cue)

    # Subtle clairvoyant/time image, chosen deterministically.
    hour = _bucket_hour(bucket)
    if hour is not None and ((mix >> 5) % 3 == 0):
        # Weave time atmospherically; hour value keeps it tied to bucket but evocative.
        time_line = _pick(_TIME_IMAGES, mix >> 7)
        if hour < 6:
            time_line = "The night hangs low... unhurried."
        elif hour < 12:
            time_line = "Morning light, barely there... taking its time."
        elif hour < 18:
            time_line = "The afternoon drifts... easy, unforced."
        else:
            time_line = "Evening settles in... slow and certain."
        # Blend with a generic image occasionally for variation.
        if (mix >> 9) % 2 == 0:
            time_line = _pick(_TIME_IMAGES, mix >> 7)
        parts.append(time_line)
    else:
        parts.append(_pick(_VISIONS, mix >> 5))

    return " ".join(parts)
