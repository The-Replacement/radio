"""Slot selection, and playout aliases that must republish inside a bucket.

The 10-minute weather refresh depends on two behaviours that did not exist:
`--slots` to render one family at a time, and a playout alias that gets
repointed when the content changes *within* the same time bucket.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from mario.gen import DEFAULT_SLOTS, SLOT_NAMES, select_slots
from mario.prune import _PROTECTED_NAMES
from mario.render import generate_rss_segment, generate_weather_segment
from mario.rss import rss_playout_alias_filename
from mario.weather import weather_playout_alias_filename
from tests.conftest import install_fake_kokoro


def test_select_slots_keeps_default_order() -> None:
    selected = select_slots("rss,weather")
    assert selected is not None
    # Request order ignored: weather always precedes rss, as in DEFAULT_SLOTS.
    assert [type(s).__name__ for s in selected] == ["WeatherGenerator", "RssGenerator"]


def test_select_slots_single_slot() -> None:
    selected = select_slots("weather")
    assert selected is not None
    assert [type(s).__name__ for s in selected] == ["WeatherGenerator"]


def test_select_slots_covers_every_default_slot() -> None:
    everything = select_slots(",".join(SLOT_NAMES))
    assert everything is not None
    assert [type(s).__name__ for s in everything] == [type(s).__name__ for s in DEFAULT_SLOTS]


def test_select_slots_tolerates_a_trailing_comma() -> None:
    # Harmless from shell loops; only genuinely unknown names are refused.
    assert select_slots("weather,") is not None


@pytest.mark.parametrize("spec", ["", "   ", "weather,news", "transitions,rss,top"])
def test_select_slots_rejects_unknown_or_empty(spec: str) -> None:
    # A typo must not fall back to "render everything": that is the O(n^2) run.
    assert select_slots(spec) is None


def test_weather_alias_republishes_within_a_bucket(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kokoro_env: tuple[Path, Path]
) -> None:
    """The bug behind frozen weather: alias published once per bucket, never again."""
    install_fake_kokoro(monkeypatch)
    bucket = "2026-11-14T14"

    model, voices = kokoro_env
    first = generate_weather_segment(
        "aaaaaaaa", bucket, "18 grados", model, tmp_path, voices_path=voices
    )
    alias = tmp_path / f"weather_{bucket}.wav"
    assert alias.is_symlink() and os.readlink(alias) == first.name

    # Same bucket, new reading -> new content-addressed file, and the alias must
    # follow it. Before this test existed, the alias stayed on the first reading
    # until the bucket flipped (up to 6 hours of stale weather).
    second = generate_weather_segment(
        "bbbbbbbb", bucket, "21 grados", model, tmp_path, voices_path=voices
    )
    assert second.name != first.name
    assert os.readlink(alias) == second.name
    assert os.readlink(tmp_path / weather_playout_alias_filename()) == second.name


def test_weather_alias_swap_is_atomic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kokoro_env: tuple[Path, Path]
) -> None:
    """No unlink-then-symlink window: playout must always resolve a real file."""
    install_fake_kokoro(monkeypatch)
    bucket = "2026-11-14T14"
    model, voices = kokoro_env
    generate_weather_segment(
        "cccccccc", bucket, "18 grados", model, tmp_path, voices_path=voices
    )
    generate_weather_segment(
        "dddddddd", bucket, "19 grados", model, tmp_path, voices_path=voices
    )

    playout = tmp_path / weather_playout_alias_filename()
    assert playout.exists(), "the playout name must never be missing between renders"
    assert not list(tmp_path.glob("*.wav.tmp")), "the temp symlink must not survive"


def test_rss_publishes_a_bucket_less_playout_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kokoro_env: tuple[Path, Path]
) -> None:
    install_fake_kokoro(monkeypatch)
    model, voices = kokoro_env
    bucket = "2026-11-14T14"
    generate_rss_segment(
        "key1",
        "clarin",
        bucket,
        "Una noticia",
        model,
        tmp_path,
        voices_path=voices,
        stable_alias=True,
    )
    playout = tmp_path / rss_playout_alias_filename()
    assert playout.is_symlink()
    assert playout.resolve().is_file()


def test_prune_protects_the_bucket_less_playout_names() -> None:
    # Pruning weather.wav would silence the meteorologist while its content
    # files still exist on disk.
    assert "weather.wav" in _PROTECTED_NAMES
    assert "rss.wav" in _PROTECTED_NAMES
