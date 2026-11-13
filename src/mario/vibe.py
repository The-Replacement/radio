"""Derive deterministic vibe (mood, pacing speed) from track metadata.

The mood/speed model is content-derived so Kokoro can vary delivery per segment
while remaining fully reproducible.
"""

from __future__ import annotations

from dataclasses import dataclass

from .tracks import TrackMeta

# Speed bounds for Kokoro delivery.
SPEED_MIN = 0.85
SPEED_MAX = 1.0

# Base speed by lane character. Unknown lanes fall back to neutral.
_LANE_SPEED: dict[str, float] = {
    "ambient": 0.87,
    "kosmische": 0.89,
    "drone": 0.87,
    "space": 0.89,
    "motorik": 0.97,
    "kraut": 0.96,
    "techno": 0.98,
    "drive": 0.97,
}

# Mood vocabulary used by text generation.
_LANE_MOOD: dict[str, str] = {
    "ambient": "hush",
    "kosmische": "drift",
    "drone": "hush",
    "space": "drift",
    "motorik": "pulse",
    "kraut": "pulse",
    "techno": "pulse",
    "drive": "pulse",
}


@dataclass(frozen=True)
class SegmentVibe:
    mood: str
    speed: float
    current_mood: str
    next_mood: str


def _norm_lane(lane: str) -> str:
    return lane.strip().lower()


def lane_mood(lane: str) -> str:
    """Return a coarse mood label for a lane."""
    key = _norm_lane(lane)
    if key in _LANE_MOOD:
        return _LANE_MOOD[key]
    # Fuzzy: match by substring for composite lane names.
    for token, mood in _LANE_MOOD.items():
        if token in key:
            return mood
    return "drift"


def _base_speed(lane: str) -> float:
    key = _norm_lane(lane)
    if key in _LANE_SPEED:
        return _LANE_SPEED[key]
    for token, speed in _LANE_SPEED.items():
        if token in key:
            return speed
    return 0.93


def _clamp_speed(value: float) -> float:
    return max(SPEED_MIN, min(SPEED_MAX, value))


def vibe_for(current: TrackMeta, nxt: TrackMeta, bucket: str) -> SegmentVibe:
    """Deterministically derive mood and TTS speed for a segment.

    Speed is biased by the incoming track's character (the vibe being cued),
    blended with the outgoing lane, then nudged by a tiny deterministic
    variation from seeds+bucket so adjacent transitions don't feel identical.
    """
    current_mood = lane_mood(current.lane)
    next_mood = lane_mood(nxt.lane)
    # Incoming lane leads the pacing; outgoing contributes lightly.
    base = 0.7 * _base_speed(nxt.lane) + 0.3 * _base_speed(current.lane)
    # Deterministic micro-variation in [-0.015, +0.015].
    mix = (current.seed * 131542391 + nxt.seed * 2654435761 + sum(ord(c) for c in bucket)) & 0xFFFFFFFF
    jitter = ((mix % 31) - 15) / 1000.0  # step 0.001, range [-0.015, 0.015]
    speed = _clamp_speed(round(base + jitter, 3))
    # Segment mood follows the incoming energy/space being cued.
    mood = next_mood
    return SegmentVibe(mood=mood, speed=speed, current_mood=current_mood, next_mood=next_mood)
