"""Render a Host segment and publish it atomically.

Kokoro backend only (kokoro-onnx, local model + voices files).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Protocol, runtime_checkable

from .liners import liner_cache_filename
from .rss import (
    rss_alias_filename,
    rss_cache_filename_from_key,
    rss_fallback_filename,
    rss_playout_alias_filename,
    rss_stable_alias_filename,
)
from .timebucket import sanitize_bucket
from .weather import (
    weather_alias_filename,
    weather_cache_filename,
    weather_playout_alias_filename,
)

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


def render_identity(
    *, text: str, template_version: str, voice: str, speed: float, lang: str
) -> str:
    """Digest of everything that decides how the WAV should sound.

    The filename stays exactly what the playout contract says it is; the
    identity lives in a sidecar next to it. The skip rule used to be "the file
    exists", so editing the phrase constants in text.py — or bumping
    MARIO_TEMPLATE_VERSION, which the docs promised would invalidate the cache —
    shipped stale audio and said nothing (issue #109).
    """
    blob = json.dumps(
        [template_version, voice, round(float(speed), 4), lang, text],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _identity_path(target: Path) -> Path:
    return target.with_name(target.name + ".meta.json")


def _stamp_identity(target: Path, identity: str) -> None:
    path = _identity_path(target)
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(json.dumps({"identity": identity}) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        logger.warning("could not stamp identity for %s (%s)", target.name, exc)


def _render_if_stale(
    target: Path,
    *,
    text: str,
    template_version: str,
    model: Path,
    force: bool,
    voices_path: Path | None,
    voice: str,
    speed: float,
    lang: str,
    backend: RenderBackend | None,
) -> Path:
    """Render ``target`` unless the audio on disk already matches this identity."""
    identity = render_identity(
        text=text,
        template_version=template_version,
        voice=voice,
        speed=speed,
        lang=lang,
    )
    if not force and target.is_file() and target.stat().st_size > 0:
        try:
            stamped = json.loads(_identity_path(target).read_text(encoding="utf-8"))
            if stamped.get("identity") == identity:
                return target
        except (OSError, ValueError):
            pass
        # No sidecar or a different identity: what is on disk does not match what
        # this code would render now, so re-render it. One pass over the legacy
        # pool, then every later run is a real cache hit again.
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
    _stamp_identity(target, identity)
    return target


def generate_segment(
    current_seed: int,
    next_seed: int,
    bucket: str,
    text: str,
    model: Path,
    host_dir: Path,
    *,
    force: bool = False,
    template_version: str = "",
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
    return _render_if_stale(
        target,
        text=text,
        template_version=template_version,
        model=model,
        force=force,
        voices_path=voices_path,
        voice=voice,
        speed=speed,
        lang=lang,
        backend=backend,
    )


def generate_liner_segment(
    ident_id: str,
    seed: int,
    bucket: str,
    text: str,
    model: Path,
    host_dir: Path,
    *,
    force: bool = False,
    template_version: str = "",
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
    target = _render_if_stale(
        target,
        text=text,
        template_version=template_version,
        model=model,
        force=force,
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
    template_version: str = "",
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
    target = _render_if_stale(
        target,
        text=text,
        template_version=template_version,
        model=model,
        force=force,
        voices_path=voices_path,
        voice=voice,
        speed=speed,
        lang=lang,
        backend=backend,
    )
    # The alias must always point at the reading selected for this run: within
    # one bucket the hash changes as the weather moves, so "publish only if the
    # alias does not exist" froze the segment for the whole bucket. Same reason
    # the per-bucket playout name is republished unconditionally.
    bucket_alias = host_dir / weather_alias_filename(bucket)
    previous = bucket_alias.resolve() if bucket_alias.is_symlink() else None
    _publish_playout_alias(host_dir, weather_alias_filename(bucket), target, force=force)
    _publish_playout_alias(host_dir, weather_playout_alias_filename(), target, force=force)
    _drop_superseded(host_dir, previous, _content_files_for("weather", bucket), keep=target)
    return target


def _content_files_for(prefix: str, bucket: str) -> re.Pattern[str]:
    """Match exactly the content-addressed files this bucket owns for a family."""
    return re.compile(rf"^{prefix}_[0-9a-f]{{8}}_{re.escape(sanitize_bucket(bucket))}\.wav$")


def _drop_superseded(
    host_dir: Path, previous: Path | None, pattern: re.Pattern[str], *, keep: Path
) -> None:
    """Unlink the content file an alias just stopped pointing at.

    Every republish leaves the previous reading behind: nothing references it
    (both aliases move in the same call, and the per-slug RSS alias too), and
    bucket retention only sweeps it when the bucket flips, which is up to six
    hours later. Deleting it keeps each family at one file per bucket.

    Safe while Liquidsoap is decoding: an open file keeps its inode on POSIX, so
    the reader finishes the segment it started.
    """
    if previous is None or previous.parent != host_dir:
        return
    # A cache hit republishes the alias it already pointed at: deleting it would
    # remove the segment that was just reused.
    if previous == keep:
        return
    if not pattern.match(previous.name):
        return
    for path in (previous, _identity_path(previous)):
        try:
            path.unlink()
        except OSError as exc:
            logger.warning("could not remove superseded %s (%s)", path.name, exc)


def _publish_playout_alias(host_dir: Path, alias_name: str, target: Path, *, force: bool) -> None:
    """Point a stable playout name at target (symlink, copy when unsupported).

    Atomic on purpose: Liquidsoap resolves this name between tracks, so it must
    never observe a window with no file (unlink-then-symlink) or a half-written
    copy. Weather now republishes every 10 minutes, which makes that window a
    certainty rather than a rare race.
    """
    alias = host_dir / alias_name
    if not force and alias.is_symlink() and os.readlink(alias) == target.name:
        return
    tmp = host_dir / (alias_name + ".tmp")
    try:
        if tmp.is_symlink() or tmp.exists():
            tmp.unlink()
        os.symlink(target.name, tmp)
        os.replace(tmp, alias)
        return
    except OSError:
        # Fallback: copy when symlinks are unsupported (some bind mounts).
        try:
            if tmp.exists():
                tmp.unlink()
            tmp.write_bytes(target.read_bytes())
            os.replace(tmp, alias)
        except OSError:
            pass


def generate_rss_segment(
    cache_key: str,
    slug: str,
    bucket: str,
    text: str,
    model: Path,
    host_dir: Path,
    *,
    force: bool = False,
    template_version: str = "",
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
    target = _render_if_stale(
        target,
        text=text,
        template_version=template_version,
        model=model,
        force=force,
        voices_path=voices_path,
        voice=voice,
        speed=speed,
        lang=lang,
        backend=backend,
    )
    # Per-feed alias: kept for debugging and for the dry-run contract.
    # It must always point at the item selected for this bucket, so a stale
    # alias is replaced even when the target file is already cached.
    bucket_alias = host_dir / rss_stable_alias_filename(bucket)
    previous = bucket_alias.resolve() if bucket_alias.is_symlink() else None
    _publish_playout_alias(host_dir, rss_alias_filename(slug, bucket), target, force=force)
    if stable_alias:
        # The per-bucket name is what playout reads: it cannot know the slug.
        _publish_playout_alias(host_dir, rss_stable_alias_filename(bucket), target, force=force)
        # And the bucket-less name, so a 30-minute refresh can land inside a
        # 6-hour bucket without waiting for the bucket to flip.
        _publish_playout_alias(host_dir, rss_playout_alias_filename(), target, force=force)
    _drop_superseded(host_dir, previous, _content_files_for("rss", bucket), keep=target)
    return target


def generate_rss_fallback_segment(
    slug: str,
    bucket: str,
    text: str,
    model: Path,
    host_dir: Path,
    *,
    force: bool = False,
    template_version: str = "",
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
    target = _render_if_stale(
        target,
        text=text,
        template_version=template_version,
        model=model,
        force=force,
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
        _publish_playout_alias(host_dir, rss_stable_alias_filename(bucket), target, force=force)
    return target
