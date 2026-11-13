"""CLI option-to-config override mapping tests (#67)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from mario.cli import app
from mario.config import MarioConfig


def _invoke_with_capture(
    monkeypatch: pytest.MonkeyPatch, args: list[str]
) -> tuple[Any, MarioConfig]:
    captured: dict[str, Any] = {}

    def fake_run(config: MarioConfig, **kwargs: Any) -> list[Path]:
        captured["config"] = config
        captured["kwargs"] = kwargs
        return []

    monkeypatch.setattr("mario.cli.run_generate", fake_run)
    result = CliRunner().invoke(app, ["generate", *args])
    assert result.exit_code == 0, result.output
    assert "config" in captured
    return result, captured["config"]


def test_cli_music_dir_sets_both_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    music = tmp_path / "music-only"
    _, cfg = _invoke_with_capture(monkeypatch, ["--music-dir", str(music)])
    assert cfg.music_dir == music
    assert cfg.meta_dir == music


def test_cli_meta_dir_overrides_music_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    music = tmp_path / "music"
    meta = tmp_path / "meta"
    # Both provided: --meta-dir takes precedence over --music-dir.
    _, cfg = _invoke_with_capture(
        monkeypatch,
        ["--music-dir", str(music), "--meta-dir", str(meta)],
    )
    assert cfg.music_dir == meta
    assert cfg.meta_dir == meta


def test_cli_simple_overrides_apply(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MARIO_VOICE", "env_voice")
    monkeypatch.setenv("MARIO_HOST_DIR", str(tmp_path / "env-host"))
    monkeypatch.setenv("MARIO_TIME_BUCKET_HOURS", "1")

    host = tmp_path / "cli-host"
    model = tmp_path / "kokoro.onnx"
    voices = tmp_path / "voices.bin"
    _, cfg = _invoke_with_capture(
        monkeypatch,
        [
            "--host-dir",
            str(host),
            "--model",
            str(model),
            "--voices-path",
            str(voices),
            "--voice",
            "ef_dora",
            "--model-lang",
            "es",
            "--time-bucket-hours",
            "2",
            "--rss-max-items-per-cycle",
            "3",
        ],
    )

    assert cfg.host_dir == host
    assert cfg.model == model
    assert cfg.voices_path == voices
    assert cfg.voice == "ef_dora"
    assert cfg.model_lang == "es"
    assert cfg.time_bucket_hours == 2
    assert cfg.rss_max_items_per_cycle == 3
