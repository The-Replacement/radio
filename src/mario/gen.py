"""Orchestration: load tracks, enumerate pairs, render all missing segments."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from .config import MarioConfig
from .feedfetch import fetch_feed
from .liners import load_liners, select_liner
from .render import (
    generate_liner_segment,
    generate_rss_fallback_segment,
    generate_rss_segment,
    generate_segment,
    generate_weather_segment,
)
from .rss import (
    DEFAULT_RSS_MAX_BYTES,
    FeedFetcher,
    RssParseError,
    build_rss_fallback_text,
    build_rss_text,
    load_feed_bytes,
    load_rss_feeds,
    load_rss_preset,
    parse_feed_items,
    rss_alias_filename,
    rss_bucket_seed,
    rss_cache_key,
    select_feed_item,
)
from .text import build_text
from .timebucket import bucket_for_now
from .tracks import load_tracks
from .vibe import vibe_for
from .weather import (
    build_weather_text,
    load_weather_preset,
    load_weather_snapshot,
    weather_cache_filename,
)

logger = logging.getLogger(__name__)


def generate(
    config: MarioConfig,
    *,
    time_bucket: str | None = None,
    dry_run: bool = False,
    force: bool = False,
    piper_bin: str = "piper",
    feed_fetcher: FeedFetcher | None = None,
) -> list[Path]:
    """Generate all missing Host segments for the current library. Returns paths."""
    tracks = load_tracks(config.music_dir)
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

    # Transition segments (per ordered pair, excluding self).
    for current in tracks:
        for nxt in tracks:
            if current.pcm_sha256 == nxt.pcm_sha256:
                continue
            text = build_text(current, nxt, bucket, config.template_version)
            vibe = vibe_for(current, nxt, bucket)
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
                speed=vibe.speed,
                lang=kokoro_lang,
            )
            out.append(path)

    # Liners: deterministic ident per track seed, cached by (ident_id, seed, time_bucket).
    liners_preset = config.liners_preset
    if liners_preset is None:
        for candidate in (
            Path("/app/presets/liners.yaml"),
            Path(__file__).resolve().parents[2] / "presets" / "liners.yaml",
            Path(__file__).resolve().parents[1] / "presets" / "liners.yaml",
        ):
            if candidate.is_file():
                liners_preset = candidate
                break
    if liners_preset is not None and liners_preset.is_file():
        pool = load_liners(liners_preset)
        for track in tracks:
            liner = select_liner(pool, track.seed)
            if dry_run:
                from .liners import liner_cache_filename

                out.append(config.host_dir / liner_cache_filename(liner.ident_id, track.seed, bucket))
                continue
            path = generate_liner_segment(
                liner.ident_id,
                track.seed,
                bucket,
                liner.text,
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

    # Weather: one deterministic segment per bucket (schedule-driven), if configured.
    if config.weather_file is not None:
        weather_preset_path = None
        for candidate in (
            Path("/app/presets/weather.yaml"),
            Path(__file__).resolve().parents[2] / "presets" / "weather.yaml",
            Path(__file__).resolve().parents[1] / "presets" / "weather.yaml",
        ):
            if candidate.is_file():
                weather_preset_path = candidate
                break
        if weather_preset_path is None:
            raise FileNotFoundError("weather preset not found")
        weather_preset = load_weather_preset(weather_preset_path)
        snapshot = load_weather_snapshot(config.weather_file)
        weather_text, weather_hash = build_weather_text(snapshot, weather_preset)
        if dry_run:
            out.append(config.host_dir / weather_cache_filename(weather_hash, bucket))
        else:
            path = generate_weather_segment(
                weather_hash,
                bucket,
                weather_text,
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

    # RSS reader: one deterministic item per configured feed, cached per bucket.
    # The feature is off unless an operator configures a feeds file.
    if config.rss_feeds_file is not None:
        rss_preset_path = None
        for candidate in (
            Path("/app/presets/rss.yaml"),
            Path(__file__).resolve().parents[2] / "presets" / "rss.yaml",
            Path(__file__).resolve().parents[1] / "presets" / "rss.yaml",
        ):
            if candidate.is_file():
                rss_preset_path = candidate
                break
        if rss_preset_path is None:
            raise FileNotFoundError("rss preset not found")
        rss_preset = load_rss_preset(rss_preset_path)
        # Slice before any fetch: the cap bounds network work, not just output.
        feeds = load_rss_feeds(config.rss_feeds_file)[: config.rss_max_items_per_cycle]
        rss_cache_dir = config.rss_cache_dir
        if rss_cache_dir is None:
            rss_cache_dir = config.host_dir.parent / "rss-cache"
        fetcher: FeedFetcher
        if feed_fetcher is not None:
            fetcher = feed_fetcher
        else:
            timeout_seconds = config.rss_timeout_seconds

            def _fetch(url: str) -> bytes | None:
                return fetch_feed(
                    url, timeout=timeout_seconds, max_bytes=DEFAULT_RSS_MAX_BYTES
                )

            fetcher = _fetch

        seed = rss_bucket_seed(bucket)
        for feed in feeds:
            if dry_run:
                # Hash-derived names are unknowable without the payload, and a
                # dry run must not touch the network: report the playout alias.
                out.append(config.host_dir / rss_alias_filename(feed.slug, bucket))
                continue
            raw = load_feed_bytes(feed, rss_cache_dir, bucket, fetcher=fetcher, force=force)
            item = None
            if raw is not None:
                try:
                    items = parse_feed_items(raw, max_text_chars=config.rss_max_text_chars)
                except RssParseError:
                    items = []
                item = select_feed_item(items, seed)
            if item is None:
                reason = "fetch-failed" if raw is None else "no-items"
                logger.warning("rss: feed %s fell back (%s)", feed.slug, reason)
                fallback_text, _fallback_hash = build_rss_fallback_text(rss_preset)
                out.append(
                    generate_rss_fallback_segment(
                        feed.slug,
                        bucket,
                        fallback_text,
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
                )
                continue
            rss_text, text_hash = build_rss_text(item, rss_preset)
            cache_key = rss_cache_key(feed.normalized_url, item.item_key, bucket, text_hash)
            out.append(
                generate_rss_segment(
                    cache_key,
                    feed.slug,
                    bucket,
                    rss_text,
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
            )

    # Preserve contract: no outputs when nothing applicable.
    if not out:
        return []

    # Write a marker of the current bucket last (atomic-ish).
    if not dry_run:
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
