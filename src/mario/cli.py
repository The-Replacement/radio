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
    dry_run: bool = typer.Option(False, "--dry-run", help="Print planned outputs without rendering."),
    force: bool = typer.Option(False, "--force", help="Re-render even if cached."),
    piper_bin: str = typer.Option("piper", "--piper-bin", help="Piper executable (piper backend)."),
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
    paths = run_generate(
        config,
        time_bucket=time_bucket,
        dry_run=dry_run,
        force=force,
        piper_bin=piper_bin,
    )
    typer.echo(f"mario: {len(paths)} segment(s) {'planned' if dry_run else 'ready'}")
    for p in paths:
        typer.echo(f"  {p}")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
