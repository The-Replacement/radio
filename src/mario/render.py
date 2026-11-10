"""Render a Host segment with Piper and publish it atomically.

Determinism relies on --noise-scale 0 and --noise-w-scale 0. Piper is invoked
as a subprocess so the model path and flags are inspectable and testable.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from .timebucket import sanitize_bucket


def cache_filename(current_seed: int, next_seed: int, bucket: str) -> str:
    return f"{current_seed}_{next_seed}_{sanitize_bucket(bucket)}.wav"


def render_wav(
    text: str,
    model: Path,
    output_path: Path,
    *,
    piper_bin: str = "piper",
) -> None:
    """Render `text` to a WAV at output_path using Piper. Raises on failure."""
    model = Path(model)
    if not model.is_file():
        raise FileNotFoundError(f"Piper model not found: {model}")
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = output_path.with_suffix(".wav.tmp")
    cmd = [
        piper_bin,
        "-m", str(model),
        "-f", str(tmp),
        "--noise-scale", "0",
        "--noise-w-scale", "0",
    ]
    try:
        proc = subprocess.run(
            cmd,
            input=text.encode("utf-8"),
            capture_output=True,
            check=False,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(f"piper binary not found: {piper_bin}") from exc
    if proc.returncode != 0:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"piper failed ({proc.returncode}): {proc.stderr.decode('utf-8', 'replace')}")
    if not tmp.is_file() or tmp.stat().st_size == 0:
        tmp.unlink(missing_ok=True)
        raise RuntimeError("piper produced an empty file")
    # fsync the rendered bytes before publishing so a crash cannot leave a
    # half-written (or stale) segment behind.
    with tmp.open("rb") as fh:
        os.fsync(fh.fileno())
    os.replace(tmp, output_path)
    # Durability of the directory entry.
    try:
        dir_fd = os.open(str(output_path.parent), os.O_DIRECTORY)
    except (OSError, AttributeError):
        dir_fd = -1
    if dir_fd >= 0:
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)


def generate_segment(
    current_seed: int,
    next_seed: int,
    bucket: str,
    text: str,
    model: Path,
    host_dir: Path,
    *,
    piper_bin: str = "piper",
    force: bool = False,
) -> Path:
    """Render (if missing) the cached segment. Returns its path."""
    host_dir = Path(host_dir)
    host_dir.mkdir(parents=True, exist_ok=True)
    name = cache_filename(current_seed, next_seed, bucket)
    target = host_dir / name
    if target.is_file() and target.stat().st_size > 0 and not force:
        return target
    render_wav(text, model, target, piper_bin=piper_bin)
    return target
