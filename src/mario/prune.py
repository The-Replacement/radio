"""Retention for Mario's pre-rendered host segment pool.

`./data/host` only ever grows: every bucket adds ``N*(N-1)`` WAVs and nothing
deletes them. This module prunes whole stale buckets so storage stays bounded
while never touching the current bucket, its aliases, or any input file.

Design notes:
- Deletion is by *bucket embedded in the filename*, never by mtime: a file's
  age on disk is not the same as the bucket it serves.
- Only files whose bucket is strictly older than the cutoff are removed, so a
  bug here cannot delete a file whose bucket is newer than the cutoff.
- Inputs (``current-bucket``, ``weather.json``, ``rss-cache/``) are never
  touched.
"""

from __future__ import annotations

import re
from pathlib import Path

_BUCKET_RE = re.compile(r"\d{4}-\d{2}-\d{2}(?:T\d{2})?")

_PROTECTED_NAMES = {"current-bucket", "current-bucket.tmp", "weather.json"}


def parse_bucket(name: str) -> str | None:
    """Return the first bucket-like token found in ``name``, or None."""
    match = _BUCKET_RE.search(name)
    return match.group(0) if match else None


def prune_host_dir(
    host_dir: Path,
    keep_buckets: int = 48,
    *,
    dry_run: bool = False,
) -> list[Path]:
    """Delete WAVs (and aliases) whose bucket is older than the kept window.

    Keeps the most recent ``keep_buckets`` distinct buckets. Returns the list of
    paths that were (or, in dry_run, would be) removed.
    """
    host_dir = Path(host_dir)
    if not host_dir.is_dir():
        return []
    if keep_buckets < 1:
        raise ValueError("keep_buckets must be >= 1")

    candidates = [
        p
        for p in host_dir.iterdir()
        if (p.is_file() or p.is_symlink()) and p.name not in _PROTECTED_NAMES
    ]

    buckets: set[str] = set()
    for p in candidates:
        bucket = parse_bucket(p.name)
        if bucket is not None:
            buckets.add(bucket)

    # Already inside the window: nothing to do.
    if len(buckets) <= keep_buckets:
        return []

    ordered = sorted(buckets)
    kept = set(ordered[-keep_buckets:])
    cutoff = ordered[-keep_buckets]  # oldest bucket still kept

    # Never delete the bucket named by the current-bucket marker, even if the
    # window math would otherwise drop it (defensive: a tiny keep_buckets on a
    # large pool).
    marker = host_dir / "current-bucket"
    if marker.is_file():
        try:
            current = marker.read_text(encoding="utf-8").strip()
        except OSError:
            current = ""
        if current:
            kept.add(current)

    deleted: list[Path] = []
    for p in candidates:
        bucket = parse_bucket(p.name)
        if bucket is None:
            continue
        if bucket in kept:
            continue
        # Safety: never delete anything not strictly older than the cutoff.
        if bucket >= cutoff:
            continue
        if not dry_run:
            try:
                p.unlink()
            except OSError:
                continue
        deleted.append(p)
    return deleted