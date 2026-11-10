"""Orchestration: load tracks, enumerate pairs, render all missing segments."""

from __future__ import annotations

import os
from pathlib import Path

from .config import MarioConfig
from .render import generate_segment
from .text import build_text
from .timebucket import bucket_for_now
from .tracks import load_tracks


def generate(
    config: MarioConfig,
    *,
    time_bucket: str | None = None,
    dry_run: bool = False,
    force: bool = False,
    piper_bin: str = "piper",
) -> list[Path]:
    """Generate all missing Host segments for the current library. Returns paths."""
    tracks = load_tracks(config.music_dir)
    if not tracks:
        return []
    bucket = time_bucket if time_bucket is not None else bucket_for_now(config.time_bucket_hours)
    out: list[Path] = []
    for current in tracks:
        for nxt in tracks:
            if current.pcm_sha256 == nxt.pcm_sha256:
                continue
            text = build_text(current, nxt, bucket, config.template_version)
            if dry_run:
                out.append(config.host_dir / f"{current.seed}_{nxt.seed}_{bucket}.wav")
                continue
            path = generate_segment(
                current.seed,
                nxt.seed,
                bucket,
                text,
                config.model,
                config.host_dir,
                piper_bin=piper_bin,
                force=force,
            )
            out.append(path)
    # Write a marker of the current bucket last (atomic-ish).
    if not dry_run and out:
        marker = config.host_dir / "current-bucket"
        tmp = config.host_dir / "current-bucket.tmp"
        tmp.write_text(bucket, encoding="utf-8")
        os.replace(tmp, marker)
    return out
