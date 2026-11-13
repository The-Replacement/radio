"""Render a Host segment and publish it atomically.

Kokoro backend only (kokoro-onnx, local model + voices files).
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import Protocol, runtime_checkable

from .liners import liner_cache_filename
from .rss import (
    rss_alias_filename,
    rss_cache_filename_from_key,
    rss_fallback_filename,
    rss_stable_alias_filename,
)
from .timebucket import sanitize_bucket
from .weather import weather_alias_filename, weather_cache_filename

logger = logging.getLogger(__name__)


@runtime_checkable
class RenderBackend(Protocol):
    """Structural protocol for TTS backends.

    A backend renders text to a WAV at output_path. The caller (render_wav)
    handles fsync/publish and missing-file cleanup. Implementations only need
    to write a valid WAV to output_path.
    """

    def render(
        self,
        text: str,
        model: Path,
        output_path: Path,
        *,
        voices_path: Path | None = None,
        voice: str = "ef_dora",
        speed: float = 1.0,
        lang: str = "es",
    ) -> None: ...


class KokoroBackend:
    """Default TTS backend: kokoro-onnx (local model + voices files)."""

    def render(
        self,
        text: str,
        model: Path,
        output_path: Path,
        *,
        voices_path: Path | None = None,
        voice: str = "ef_dora",
        speed: float = 1.0,
        lang: str = "es",
    ) -> None:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            dir=output_path.parent,
            prefix=f".{output_path.stem}.",
            suffix=".wav.tmp",
        )
        os.close(fd)
        tmp = Path(tmp_name)
        os.chmod(tmp, 0o644)
        try:
            _render_kokoro(
                text,
                model,
                tmp,
                voices_path=voices_path,
                voice=voice,
                speed=speed,
                lang=lang,
            )
            _fsync_publish(tmp, output_path)
        except Exception:
            tmp.unlink(missing_ok=True)
            raise


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
        import soundfile as sf  # type: ignore[import-not-found,import-untyped,unused-ignore]
        from kokoro_onnx import Kokoro  # type: ignore[import-not-found,import-untyped,unused-ignore]
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
    voices_path: Path | None = None,
    voice: str = "ef_dora",
    speed: float = 1.0,
    lang: str = "es",
    backend: RenderBackend | None = None,
) -> None:
    """Render `text` to a WAV at output_path. Raises on failure.

    backend: optional RenderBackend. Defaults to KokoroBackend() when None.
    """
    if backend is None:
        backend = KokoroBackend()
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    backend.render(
        text,
        model,
        output_path,
        voices_path=voices_path,
        voice=voice,
        speed=speed,
        lang=lang,
    )


def generate_segment(
    current_seed: int,
    next_seed: int,
    bucket: str,
    text: str,
    model: Path,
    host_dir: Path,
    *,
    force: bool = False,
    voices_path: Path | None = None,
    voice: str = "ef_dora",
    speed: float = 1.0,
    lang: str = "es",
    backend: RenderBackend | None = None,
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
        voices_path=voices_path,
        voice=voice,
        speed=speed,
        lang=lang,
        backend=backend,
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
    force: bool = False,
    voices_path: Path | None = None,
    voice: str = "ef_dora",
    speed: float = 1.0,
    lang: str = "es",
    backend: RenderBackend | None = None,
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
            voices_path=voices_path,
            voice=voice,
            speed=speed,
            lang=lang,
            backend=backend,
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


def generate_weather_segment(
    hash_hex: str,
    bucket: str,
    text: str,
    model: Path,
    host_dir: Path,
    *,
    force: bool = False,
    voices_path: Path | None = None,
    voice: str = "ef_dora",
    speed: float = 1.0,
    lang: str = "es",
    backend: RenderBackend | None = None,
) -> Path:
    """Render (if missing) a cached weather segment keyed by (weather_hash, time_bucket)."""
    host_dir = Path(host_dir)
    host_dir.mkdir(parents=True, exist_ok=True)
    name = weather_cache_filename(hash_hex, bucket)
    target = host_dir / name
    if not (target.is_file() and target.stat().st_size > 0 and not force):
        render_wav(
            text,
            model,
            target,
            voices_path=voices_path,
            voice=voice,
            speed=speed,
            lang=lang,
            backend=backend,
        )
    alias = host_dir / weather_alias_filename(bucket)
    if force or not alias.exists():
        try:
            if alias.is_symlink() or alias.exists():
                alias.unlink()
            os.symlink(target.name, alias)
        except OSError:
            if force or not (alias.is_file() and alias.stat().st_size > 0):
                alias.write_bytes(target.read_bytes())
    return target


def _publish_playout_alias(
    host_dir: Path, alias_name: str, target: Path, *, force: bool
) -> None:
    """Point a stable playout name at target (symlink, copy when unsupported)."""
    alias = host_dir / alias_name
    if not force and alias.is_symlink() and os.readlink(alias) == target.name:
        return
    try:
        if alias.is_symlink() or alias.exists():
            alias.unlink()
        os.symlink(target.name, alias)
    except OSError:
        # Fallback: hard copy if symlinks are unsupported.
        alias.write_bytes(target.read_bytes())


def generate_rss_segment(
    cache_key: str,
    slug: str,
    bucket: str,
    text: str,
    model: Path,
    host_dir: Path,
    *,
    force: bool = False,
    voices_path: Path | None = None,
    voice: str = "ef_dora",
    speed: float = 1.0,
    lang: str = "es",
    backend: RenderBackend | None = None,
    stable_alias: bool = False,
) -> Path:
    """Render (if missing) a cached RSS segment keyed by (cache_key, time_bucket)."""
    host_dir = Path(host_dir)
    host_dir.mkdir(parents=True, exist_ok=True)
    name = rss_cache_filename_from_key(cache_key, bucket)
    target = host_dir / name
    if not (target.is_file() and target.stat().st_size > 0 and not force):
        render_wav(
            text,
            model,
            target,
            voices_path=voices_path,
            voice=voice,
            speed=speed,
            lang=lang,
            backend=backend,
        )
    # Per-feed alias: kept for debugging and for the dry-run contract.
    # It must always point at the item selected for this bucket, so a stale
    # alias is replaced even when the target file is already cached.
    _publish_playout_alias(host_dir, rss_alias_filename(slug, bucket), target, force=force)
    if stable_alias:
        # The per-bucket name is what playout reads: it cannot know the slug.
        _publish_playout_alias(
            host_dir, rss_stable_alias_filename(bucket), target, force=force
        )
    return target


def generate_rss_fallback_segment(
    slug: str,
    bucket: str,
    text: str,
    model: Path,
    host_dir: Path,
    *,
    force: bool = False,
    voices_path: Path | None = None,
    voice: str = "ef_dora",
    speed: float = 1.0,
    lang: str = "es",
    backend: RenderBackend | None = None,
    stable_alias: bool = False,
) -> Path:
    """Render (if missing) the per-feed fallback segment and drop any stale item alias."""
    host_dir = Path(host_dir)
    host_dir.mkdir(parents=True, exist_ok=True)
    target = host_dir / rss_fallback_filename(slug, bucket)
    if not (target.is_file() and target.stat().st_size > 0 and not force):
        render_wav(
            text,
            model,
            target,
            voices_path=voices_path,
            voice=voice,
            speed=speed,
            lang=lang,
            backend=backend,
        )
    # The fallback file already carries the stable name; make sure a previously
    # published item cannot keep playing instead of it.
    alias = host_dir / rss_alias_filename(slug, bucket)
    try:
        alias.unlink(missing_ok=True)
    except OSError as exc:
        logger.warning("rss: could not remove stale alias %s (%s)", alias.name, exc)
    if stable_alias:
        _publish_playout_alias(
            host_dir, rss_stable_alias_filename(bucket), target, force=force
        )
    return target
