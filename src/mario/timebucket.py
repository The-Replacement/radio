"""Quantize a datetime into a stable bucket string.

The bucket is an explicit input to generation: it makes "the time" announcement
deterministic and reproducible, at the cost of real-time precision.
"""

from __future__ import annotations

from datetime import UTC, datetime


def quantize_bucket(now: datetime, hours: int = 1) -> str:
    """Quantize `now` to a bucket of `hours` width.

    Returns a string like "2026-11-10T14" (hour bucket) or "2026-11-10" (day).
    """
    if hours < 1:
        raise ValueError("hours must be >= 1")
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    # Floor to the bucket boundary.
    total_minutes = now.hour * 60 + now.minute
    bucket_minutes = (total_minutes // (hours * 60)) * (hours * 60)
    bh = bucket_minutes // 60
    bm = bucket_minutes % 60
    dt = now.replace(hour=bh, minute=bm, second=0, microsecond=0)
    if hours >= 24:
        return dt.strftime("%Y-%m-%d")
    if hours >= 1:
        return dt.strftime("%Y-%m-%dT%H")
    return dt.isoformat()


def bucket_for_now(hours: int = 1, now: datetime | None = None) -> str:
    if now is None:
        now = datetime.now(UTC)
    return quantize_bucket(now, hours)


def sanitize_bucket(bucket: str) -> str:
    return bucket.replace(":", "-").replace("/", "_")
