"""Mario CLI."""

from __future__ import annotations

from pathlib import Path

import typer

from .config import MarioConfig
from .gen import generate as run_generate

app = typer.Typer(help="Mario — the host voice for the radio.")


@app.callback()
def _root() -> None:
    """Mario — the host voice for the radio."""


@app.command("generate")
def generate_cmd(
    music_dir: Path | None = typer.Option(None, "--music-dir", help="Radio music directory."),
    host_dir: Path | None = typer.Option(None, "--host-dir", help="Output directory for Host segments."),
    model: Path | None = typer.Option(None, "--model", help="TTS model path (piper .onnx or kokoro .onnx)."),
    voices_path: Path | None = typer.Option(None, "--voices-path", help="Kokoro voices .bin path."),
    voice: str | None = typer.Option(None, "--voice", help="Kokoro voice name (e.g. af_sarah)."),
    backend: str | None = typer.Option(None, "--backend", help="TTS backend: piper or kokoro."),
    model_lang: str | None = typer.Option(None, "--model-lang", help="Language hint (e.g. es, en-us)."),
    time_bucket: str | None = typer.Option(None, "--time-bucket", help="Override the time bucket (e.g. 2026-11-10T14)."),
    time_bucket_hours: int | None = typer.Option(None, "--time-bucket-hours", help="Bucket width in hours."),
    liners_preset: Path | None = typer.Option(None, "--liners-preset", help="Path to liners preset YAML (station idents)."),
    weather_file: Path | None = typer.Option(None, "--weather-file", help="Path to local weather snapshot JSON."),
    rss_feeds_file: Path | None = typer.Option(None, "--rss-feeds-file", help="Path to RSS feeds config (YAML/JSON)."),
    rss_cache_dir: Path | None = typer.Option(None, "--rss-cache-dir", help="Directory for cached feed payloads."),
    rss_max_items_per_cycle: int | None = typer.Option(None, "--rss-max-items-per-cycle", help="Max feeds read per cycle."),
    rss_max_text_chars: int | None = typer.Option(None, "--rss-max-text-chars", help="Max spoken characters per feed item."),
    rss_timeout_seconds: float | None = typer.Option(None, "--rss-timeout-seconds", help="Feed fetch timeout in seconds."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print planned outputs without rendering."),
    force: bool = typer.Option(False, "--force", help="Re-render even if cached."),
    piper_bin: str = typer.Option("piper", "--piper-bin", help="Piper executable (piper backend)."),
    keep_buckets: int | None = typer.Option(None, "--keep-buckets", help="Prune host pool to this many recent buckets after generating."),
) -> None:
    config = MarioConfig.from_env()
    if music_dir is not None:
        config.music_dir = music_dir
    if host_dir is not None:
        config.host_dir = host_dir
    if model is not None:
        config.model = model
    if voices_path is not None:
        config.voices_path = voices_path
    if voice is not None:
        config.voice = voice
    if backend is not None:
        config.tts_backend = backend
    if model_lang is not None:
        config.model_lang = model_lang
    if time_bucket_hours is not None:
        config.time_bucket_hours = time_bucket_hours
    if liners_preset is not None:
        config.liners_preset = liners_preset
    if weather_file is not None:
        config.weather_file = weather_file
    if rss_feeds_file is not None:
        config.rss_feeds_file = rss_feeds_file
    if rss_cache_dir is not None:
        config.rss_cache_dir = rss_cache_dir
    if rss_max_items_per_cycle is not None:
        config.rss_max_items_per_cycle = rss_max_items_per_cycle
    if rss_max_text_chars is not None:
        config.rss_max_text_chars = rss_max_text_chars
    if rss_timeout_seconds is not None:
        config.rss_timeout_seconds = rss_timeout_seconds
    paths = run_generate(
        config,
        time_bucket=time_bucket,
        dry_run=dry_run,
        force=force,
        piper_bin=piper_bin,
        keep_buckets=keep_buckets,
    )
    typer.echo(f"mario: {len(paths)} segment(s) {'planned' if dry_run else 'ready'}")
    for p in paths:
        typer.echo(f"  {p}")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
