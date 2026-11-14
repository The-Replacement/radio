"""Mario CLI."""

from __future__ import annotations

from pathlib import Path

import typer

from .config import MarioConfig
from .gen import SLOT_NAMES, select_slots
from .gen import generate as run_generate
from .openmeteo import DEFAULT_LAT, DEFAULT_LON, fetch_snapshot, write_snapshot

app = typer.Typer(help="Mario — the host voice for the radio.")


@app.callback()
def _root() -> None:
    """Mario — the host voice for the radio."""


@app.command("weather-fetch")
def weather_fetch_cmd(
    lat: float = typer.Option(DEFAULT_LAT, "--lat", help="Latitude to fetch."),
    lon: float = typer.Option(DEFAULT_LON, "--lon", help="Longitude to fetch."),
    out: Path = typer.Option(
        Path("/data/host/weather.json"), "--out", help="Snapshot path mario generate reads."
    ),
    timeout: float = typer.Option(10.0, "--timeout", help="HTTP timeout in seconds."),
) -> None:
    """Fetch current weather from Open-Meteo and write the snapshot atomically.

    On any failure the previous snapshot is left untouched and the exit code is
    non-zero, so cron logs a real failure instead of a silent no-op.
    """
    snapshot = fetch_snapshot(lat=lat, lon=lon, timeout=timeout)
    if snapshot is None:
        typer.echo("mario: weather fetch failed; previous snapshot left untouched", err=True)
        raise typer.Exit(code=1)
    write_snapshot(snapshot, out)
    typer.echo(
        f"mario: weather {snapshot.temperature_c}C "
        f"{snapshot.sky} wind {snapshot.wind_speed_kmh}km/h "
        f"from {snapshot.wind_dir_deg}deg -> {out}"
    )


@app.command("generate")
def generate_cmd(
    music_dir: Path | None = typer.Option(None, "--music-dir", help="Radio music/metadata directory."),
    meta_dir: Path | None = typer.Option(None, "--meta-dir", help="Metadata directory (catalog/sidecars). Overrides --music-dir."),
    host_dir: Path | None = typer.Option(None, "--host-dir", help="Output directory for Host segments."),
    model: Path | None = typer.Option(None, "--model", help="Kokoro model .onnx path."),
    voices_path: Path | None = typer.Option(None, "--voices-path", help="Kokoro voices .bin path."),
    voice: str | None = typer.Option(None, "--voice", help="Kokoro voice name (e.g. ef_dora)."),
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
    no_marker: bool = typer.Option(
        False,
        "--no-marker",
        help="Do not rewrite <host-dir>/current-bucket. Required for single-slot runs, "
        "which may use a different bucket width than the families playout resolves.",
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print planned outputs without rendering."),
    force: bool = typer.Option(False, "--force", help="Re-render even if cached."),
    keep_buckets: int | None = typer.Option(None, "--keep-buckets", help="Prune host pool to this many recent buckets after generating."),
    slots: str | None = typer.Option(
        None,
        "--slots",
        help="Comma-separated subset of slots to render: transitions,liners,weather,rss. "
        "Default: all four. Lets weather/news refresh on their own cadence inside a bucket.",
    ),
) -> None:
    config = MarioConfig.from_env()

    # Paired directory overrides preserve intentional precedence:
    # --music-dir sets both, then --meta-dir sets both (wins if both provided).
    if music_dir is not None:
        config.music_dir = music_dir
        config.meta_dir = music_dir
    if meta_dir is not None:
        config.meta_dir = meta_dir
        config.music_dir = meta_dir

    simple_overrides = {
        "host_dir": host_dir,
        "model": model,
        "voices_path": voices_path,
        "voice": voice,
        "model_lang": model_lang,
        "time_bucket_hours": time_bucket_hours,
        "liners_preset": liners_preset,
        "weather_file": weather_file,
        "rss_feeds_file": rss_feeds_file,
        "rss_cache_dir": rss_cache_dir,
        "rss_max_items_per_cycle": rss_max_items_per_cycle,
        "rss_max_text_chars": rss_max_text_chars,
        "rss_timeout_seconds": rss_timeout_seconds,
    }
    for field, value in simple_overrides.items():
        if value is not None:
            setattr(config, field, value)

    if slots:
        selected = select_slots(slots)
        if selected is None:
            typer.echo(
                f"mario: unknown slot in --slots {slots!r}; "
                f"choose from {', '.join(SLOT_NAMES)}",
                err=True,
            )
            raise typer.Exit(code=2)
    else:
        selected = None

    paths = run_generate(
        config,
        time_bucket=time_bucket,
        dry_run=dry_run,
        force=force,
        keep_buckets=keep_buckets,
        slots=selected,
        write_marker=not no_marker,
    )
    typer.echo(f"mario: {len(paths)} segment(s) {'planned' if dry_run else 'ready'}")
    for p in paths:
        typer.echo(f"  {p}")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
