"""Render a Host segment and publish it atomically.

Backends:
- piper (default): subprocess, deterministic with --noise-scale 0/--noise-w-scale 0.
- kokoro: kokoro-onnx Python API (local model+voices files).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from .liners import liner_cache_filename
from .timebucket import sanitize_bucket


def cache_filename(current_seed: int, next_seed: int, bucket: str) -> str:
    return f"{current_seed}_{next_seed}_{sanitize_bucket(bucket)}.wav"


def _fsync_publish(tmp: Path, output_path: Path) -> None:
    if not tmp.is_file() or tmp.stat().st_size == 0:
        tmp.unlink(missing_ok=True)
        raise RuntimeError("tts produced an empty file")
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


def _render_piper(text: str, model: Path, tmp: Path, *, piper_bin: str = "piper") -> None:
    model = Path(model)
    if not model.is_file():
        raise FileNotFoundError(f"Piper model not found: {model}")
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


def _render_kokoro(
    text: str,
    model: Path,
    tmp: Path,
    *,
    voices_path: Path | None,
    voice: str,
    speed: float,
    lang: str,
) -> None:
    model = Path(model)
    if voices_path is None:
        raise FileNotFoundError("Kokoro voices_path is required")
    voices_path = Path(voices_path)
    if not model.is_file():
        raise FileNotFoundError(f"Kokoro model not found: {model}")
    if not voices_path.is_file():
        raise FileNotFoundError(f"Kokoro voices file not found: {voices_path}")
    try:
        import soundfile as sf  # type: ignore[import-not-found]
        from kokoro_onnx import Kokoro  # type: ignore[import-not-found]
    except Exception as exc:  # pragma: no cover - import error path
        raise RuntimeError(
            "kokoro backend requires kokoro-onnx and soundfile to be installed"
        ) from exc
    kokoro = Kokoro(str(model), str(voices_path))
    samples, sample_rate = kokoro.create(text, voice=voice, speed=speed, lang=lang)
    sf.write(str(tmp), samples, sample_rate, format="WAV")


def render_wav(
    text: str,
    model: Path,
    output_path: Path,
    *,
    piper_bin: str = "piper",
    backend: str = "piper",
    voices_path: Path | None = None,
    voice: str = "af_sarah",
    speed: float = 1.0,
    lang: str = "en-us",
) -> None:
    """Render `text` to a WAV at output_path. Raises on failure."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = output_path.with_suffix(".wav.tmp")
    try:
        if backend == "piper":
            _render_piper(text, model, tmp, piper_bin=piper_bin)
        elif backend == "kokoro":
            _render_kokoro(
                text,
                model,
                tmp,
                voices_path=voices_path,
                voice=voice,
                speed=speed,
                lang=lang,
            )
        else:
            raise ValueError(f"unknown tts backend: {backend}")
        _fsync_publish(tmp, output_path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


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
    backend: str = "piper",
    voices_path: Path | None = None,
    voice: str = "af_sarah",
    speed: float = 1.0,
    lang: str = "en-us",
) -> Path:
    """Render (if missing) the cached transition segment. Returns its path."""
    host_dir = Path(host_dir)
    host_dir.mkdir(parents=True, exist_ok=True)
    name = cache_filename(current_seed, next_seed, bucket)
    target = host_dir / name
    if target.is_file() and target.stat().st_size > 0 and not force:
        return target
    render_wav(
        text,
        model,
        target,
        piper_bin=piper_bin,
        backend=backend,
        voices_path=voices_path,
        voice=voice,
        speed=speed,
        lang=lang,
    )
    return target


def generate_liner_segment(
    ident_id: str,
    seed: int,
    bucket: str,
    text: str,
    model: Path,
    host_dir: Path,
    *,
    piper_bin: str = "piper",
    force: bool = False,
    backend: str = "piper",
    voices_path: Path | None = None,
    voice: str = "af_sarah",
    speed: float = 1.0,
    lang: str = "en-us",
) -> Path:
    """Render (if missing) a cached liner segment keyed by (ident_id, seed, time_bucket)."""
    host_dir = Path(host_dir)
    host_dir.mkdir(parents=True, exist_ok=True)
    name = liner_cache_filename(ident_id, seed, bucket)
    target = host_dir / name
    if not (target.is_file() and target.stat().st_size > 0 and not force):
        render_wav(
            text,
            model,
            target,
            piper_bin=piper_bin,
            backend=backend,
            voices_path=voices_path,
            voice=voice,
            speed=speed,
            lang=lang,
        )
    # Stable alias for playout systems: deterministic selection by seed means
    # this file is the liner for (seed, bucket).
    from .timebucket import sanitize_bucket

    alias = host_dir / f"liner_{seed}_{sanitize_bucket(bucket)}.wav"
    if force or not alias.exists():
        try:
            if alias.is_symlink() or alias.exists():
                alias.unlink()
            os.symlink(target.name, alias)
        except OSError:
            # Fallback: hard copy if symlinks unsupported.
            if force or not (alias.is_file() and alias.stat().st_size > 0):
                alias.write_bytes(target.read_bytes())
    return target
