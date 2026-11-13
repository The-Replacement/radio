"""Orchestration: load tracks, enumerate pairs, render all missing segments."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from .config import MarioConfig
from .feedfetch import fetch_feed
from .liners import liner_cache_filename, load_liners, select_liner
from .presets import require_preset_path, resolve_preset_path
from .prune import prune_host_dir
from .render import (
    RenderBackend,
    generate_liner_segment,
    generate_rss_fallback_segment,
    generate_rss_segment,
    generate_segment,
    generate_weather_segment,
)
from .rss import (
    DEFAULT_RSS_MAX_BYTES,
    FeedConfig,
    FeedFetcher,
    FeedItem,
    RssParseError,
    build_rss_fallback_text,
    build_rss_text,
    load_feed_bytes,
    load_rss_feeds,
    load_rss_preset,
    parse_feed_items,
    rss_bucket_seed,
    rss_cache_key,
    rss_stable_alias_filename,
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

# Stop walking the feed list once this many fetches in a row fail outright:
# a dead network should not cost one timeout per configured feed.
RSS_MAX_CONSECUTIVE_FAILURES = 2


@dataclass(frozen=True)
class _GenerationContext:
    config: MarioConfig
    tracks: list[Any]
    bucket: str
    kokoro_lang: str
    dry_run: bool
    force: bool
    feed_fetcher: FeedFetcher | None
    keep_buckets: int | None
    backend: RenderBackend | None = None


class _SegmentSink:
    """Unifies dry-run path recording and real render calls for host segments."""

    def __init__(self, ctx: _GenerationContext, out: list[Path]) -> None:
        self.ctx = ctx
        self.out = out
        self.host_dir = ctx.config.host_dir
        self.bucket = ctx.bucket
        self.backend = ctx.backend

    @property
    def dry_run(self) -> bool:
        return self.ctx.dry_run

    @property
    def _tts_kwargs(self) -> dict[str, Any]:
        return {
            "force": self.ctx.force,
            "voices_path": self.ctx.config.voices_path,
            "voice": self.ctx.config.voice,
            "lang": self.ctx.kokoro_lang,
            "backend": self.backend,
        }

    def record(self, path: Path) -> Path:
        self.out.append(path)
        return path

    def transition(self, current_seed: int, next_seed: int, text: str, speed: float) -> Path:
        if self.dry_run:
            return self.record(self.host_dir / f"{current_seed}_{next_seed}_{self.bucket}.wav")
        return self.record(
            generate_segment(
                current_seed,
                next_seed,
                self.bucket,
                text,
                self.ctx.config.model,
                self.host_dir,
                speed=speed,
                **self._tts_kwargs,
            )
        )

    def liner(self, ident_id: str, seed: int, text: str) -> Path:
        if self.dry_run:
            return self.record(
                self.host_dir / liner_cache_filename(ident_id, seed, self.bucket)
            )
        return self.record(
            generate_liner_segment(
                ident_id,
                seed,
                self.bucket,
                text,
                self.ctx.config.model,
                self.host_dir,
                speed=self.ctx.config.tts_speed,
                **self._tts_kwargs,
            )
        )

    def weather(self, weather_hash: str, text: str) -> Path:
        if self.dry_run:
            return self.record(
                self.host_dir / weather_cache_filename(weather_hash, self.bucket)
            )
        return self.record(
            generate_weather_segment(
                weather_hash,
                self.bucket,
                text,
                self.ctx.config.model,
                self.host_dir,
                speed=self.ctx.config.tts_speed,
                **self._tts_kwargs,
            )
        )

    def rss_stable_alias(self) -> Path:
        return self.record(self.host_dir / rss_stable_alias_filename(self.bucket))

    def rss(self, cache_key: str, slug: str, text: str, *, stable_alias: bool) -> Path:
        return self.record(
            generate_rss_segment(
                cache_key,
                slug,
                self.bucket,
                text,
                self.ctx.config.model,
                self.host_dir,
                speed=self.ctx.config.tts_speed,
                stable_alias=stable_alias,
                **self._tts_kwargs,
            )
        )

    def rss_fallback(self, slug: str, text: str, *, stable_alias: bool) -> Path:
        return self.record(
            generate_rss_fallback_segment(
                slug,
                self.bucket,
                text,
                self.ctx.config.model,
                self.host_dir,
                speed=self.ctx.config.tts_speed,
                stable_alias=stable_alias,
                **self._tts_kwargs,
            )
        )


def _resolve_kokoro_lang(model_lang: str) -> str:
    # Map config model_lang (e.g. "es", "en") to kokoro lang tags.
    if model_lang.startswith("en-gb") or model_lang == "b":
        return "en-gb"
    if model_lang.startswith("en"):
        return "en-us"
    if model_lang.startswith("es"):
        return "es"
    return model_lang


def _write_bucket_marker(host_dir: Path, bucket: str) -> None:
    # Write a marker of the current bucket last (atomic-ish).
    marker = host_dir / "current-bucket"
    tmp = host_dir / "current-bucket.tmp"
    tmp.write_text(bucket, encoding="utf-8")
    with tmp.open("rb") as fh:
        os.fsync(fh.fileno())
    os.replace(tmp, marker)
    try:
        dir_fd = os.open(str(host_dir), os.O_DIRECTORY)
    except (OSError, AttributeError):
        dir_fd = -1
    if dir_fd >= 0:
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)


@runtime_checkable
class SlotGenerator(Protocol):
    """Structural protocol for segment slot generators.

    A slot generator emits segments for one domain (transitions, liners,
    weather, RSS) into the shared sink. Implementations are stateless and
    can be registered in the DEFAULT_SLOTS list or passed via generate(slots=...).
    """

    def emit(self, ctx: _GenerationContext, sink: _SegmentSink) -> None: ...


class TransitionGenerator:
    """Generates transition segments (per ordered track pair)."""

    def emit(self, ctx: _GenerationContext, sink: _SegmentSink) -> None:
        _emit_transitions(ctx, sink)


class LinerGenerator:
    """Generates liner/ident segments (per track)."""

    def emit(self, ctx: _GenerationContext, sink: _SegmentSink) -> None:
        _emit_liners(ctx, sink)


class WeatherGenerator:
    """Generates weather segment (one per bucket)."""

    def emit(self, ctx: _GenerationContext, sink: _SegmentSink) -> None:
        _emit_weather(ctx, sink)


class RssGenerator:
    """Generates RSS/news segments (per cycle)."""

    def emit(self, ctx: _GenerationContext, sink: _SegmentSink) -> None:
        _emit_rss(ctx, sink)


# Default slot order: transitions → liners → weather → RSS.
DEFAULT_SLOTS: list[SlotGenerator] = [
    TransitionGenerator(),
    LinerGenerator(),
    WeatherGenerator(),
    RssGenerator(),
]


def _emit_transitions(ctx: _GenerationContext, sink: _SegmentSink) -> None:
    # Transition segments (per ordered pair, excluding self).
    for current in ctx.tracks:
        for nxt in ctx.tracks:
            if current.pcm_sha256 == nxt.pcm_sha256:
                continue
            text = build_text(current, nxt, ctx.bucket, ctx.config.template_version)
            vibe = vibe_for(current, nxt, ctx.bucket)
            sink.transition(current.seed, nxt.seed, text, vibe.speed)


def _emit_liners(ctx: _GenerationContext, sink: _SegmentSink) -> None:
    # Liners: deterministic ident per track seed, cached by (ident_id, seed, time_bucket).
    liners_preset = ctx.config.liners_preset
    if liners_preset is None:
        liners_preset = resolve_preset_path("liners.yaml")
    if liners_preset is None or not liners_preset.is_file():
        return
    pool = load_liners(liners_preset)
    for track in ctx.tracks:
        liner = select_liner(pool, track.seed)
        sink.liner(liner.ident_id, track.seed, liner.text)


def _emit_weather(ctx: _GenerationContext, sink: _SegmentSink) -> None:
    # Weather: one deterministic segment per bucket (schedule-driven), if configured.
    if ctx.config.weather_file is None:
        return
    weather_preset_path = require_preset_path("weather.yaml")
    weather_preset = load_weather_preset(weather_preset_path)
    snapshot = load_weather_snapshot(ctx.config.weather_file)
    weather_text, weather_hash = build_weather_text(snapshot, weather_preset)
    sink.weather(weather_hash, weather_text)


def _rss_fetcher(ctx: _GenerationContext) -> FeedFetcher:
    if ctx.feed_fetcher is not None:
        return ctx.feed_fetcher
    timeout_seconds = ctx.config.rss_timeout_seconds

    def _fetch(url: str) -> bytes | None:
        return fetch_feed(url, timeout=timeout_seconds, max_bytes=DEFAULT_RSS_MAX_BYTES)

    return _fetch


def _emit_rss(ctx: _GenerationContext, sink: _SegmentSink) -> None:
    # RSS reader: one deterministic news item per cycle, rotating over the
    # configured feeds. The feature is off unless an operator sets a feeds file.
    if ctx.config.rss_feeds_file is None:
        return
    rss_preset_path = require_preset_path("rss.yaml")
    rss_preset = load_rss_preset(rss_preset_path)
    all_feeds = load_rss_feeds(ctx.config.rss_feeds_file)
    rss_cache_dir = ctx.config.rss_cache_dir
    if rss_cache_dir is None:
        rss_cache_dir = ctx.config.host_dir.parent / "rss-cache"
    fetcher = _rss_fetcher(ctx)

    seed = rss_bucket_seed(ctx.bucket)
    # Rotate the starting feed per bucket. Without this the first N entries
    # in the file win every hour and the rest never air.
    start = seed % len(all_feeds)
    ordered = all_feeds[start:] + all_feeds[:start]

    if ctx.dry_run:
        # A dry run must not touch the network, and which feeds have items
        # is unknowable without fetching. Report the name playout reads.
        sink.rss_stable_alias()
        return

    airing: list[tuple[FeedConfig, FeedItem]] = []
    failures = 0
    for feed in ordered:
        if len(airing) >= ctx.config.rss_max_items_per_cycle:
            break
        raw = load_feed_bytes(feed, rss_cache_dir, ctx.bucket, fetcher=fetcher, force=ctx.force)
        if raw is None:
            failures += 1
            logger.warning("rss: feed %s fetch failed", feed.slug)
            if failures >= RSS_MAX_CONSECUTIVE_FAILURES:
                logger.warning("rss: stopping after %d consecutive fetch failures", failures)
                break
            continue
        failures = 0
        try:
            items = parse_feed_items(raw, max_text_chars=ctx.config.rss_max_text_chars)
        except RssParseError:
            items = []
        item = select_feed_item(items, seed)
        if item is None:
            # Empty or unreadable feed: skip it. Announcing "no news"
            # for every dead category is worse than saying nothing.
            logger.info("rss: feed %s has no items; skipped", feed.slug)
            continue
        airing.append((feed, item))

    if not airing:
        # Every candidate failed this cycle: publish one fallback so the
        # news slot says something rather than going quiet.
        fallback_text, _fallback_hash = build_rss_fallback_text(rss_preset)
        sink.rss_fallback(ordered[0].slug, fallback_text, stable_alias=True)
        return

    for index, (feed, item) in enumerate(airing):
        rss_text, text_hash = build_rss_text(item, rss_preset)
        cache_key = rss_cache_key(feed.normalized_url, item.item_key, ctx.bucket, text_hash)
        sink.rss(cache_key, feed.slug, rss_text, stable_alias=index == 0)


def generate(
    config: MarioConfig,
    *,
    time_bucket: str | None = None,
    dry_run: bool = False,
    force: bool = False,
    feed_fetcher: FeedFetcher | None = None,
    keep_buckets: int | None = None,
    backend: RenderBackend | None = None,
    slots: list[SlotGenerator] | None = None,
) -> list[Path]:
    """Generate all missing Host segments for the current library. Returns paths."""
    tracks = load_tracks(config.metadata_dir)
    bucket = time_bucket if time_bucket is not None else bucket_for_now(config.time_bucket_hours)
    ctx = _GenerationContext(
        config=config,
        tracks=tracks,
        bucket=bucket,
        kokoro_lang=_resolve_kokoro_lang(config.model_lang),
        dry_run=dry_run,
        force=force,
        feed_fetcher=feed_fetcher,
        keep_buckets=keep_buckets,
        backend=backend,
    )
    out: list[Path] = []
    sink = _SegmentSink(ctx, out)

    if slots is None:
        slots = DEFAULT_SLOTS
    for slot in slots:
        slot.emit(ctx, sink)

    # Preserve contract: no outputs when nothing applicable.
    if not out:
        return []

    if not ctx.dry_run:
        _write_bucket_marker(ctx.config.host_dir, ctx.bucket)
    if not ctx.dry_run and ctx.keep_buckets is not None:
        prune_host_dir(ctx.config.host_dir, ctx.keep_buckets)
    return out
