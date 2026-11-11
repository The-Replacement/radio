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
    # Map config model_lang (e.g. "es", "en") to kokoro lang tags.
    kokoro_lang = "en-us"
    if config.model_lang.startswith("en-gb") or config.model_lang == "b":
        kokoro_lang = "en-gb"
    elif config.model_lang.startswith("en"):
        kokoro_lang = "en-us"
    elif config.model_lang.startswith("es"):
        kokoro_lang = "es"
    else:
        kokoro_lang = config.model_lang
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
                backend=config.tts_backend,
                voices_path=config.voices_path,
                voice=config.voice,
                speed=config.tts_speed,
                lang=kokoro_lang,
            )
            out.append(path)
    # Write a marker of the current bucket last (atomic-ish).
    if not dry_run and out:
        marker = config.host_dir / "current-bucket"
        tmp = config.host_dir / "current-bucket.tmp"
        tmp.write_text(bucket, encoding="utf-8")
        with tmp.open("rb") as fh:
            os.fsync(fh.fileno())
        os.replace(tmp, marker)
        try:
            dir_fd = os.open(str(config.host_dir), os.O_DIRECTORY)
        except (OSError, AttributeError):
            dir_fd = -1
        if dir_fd >= 0:
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
    return out
