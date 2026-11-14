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


def test_single_slot_run_does_not_move_the_marker(tmp_path: Path) -> None:
    """Playout builds every transition path from current-bucket.

    A news run uses a 30-minute bucket so the feed rotation changes; if that run
    also wrote the marker, every transition and liner lookup would target
    names that do not exist and the station would fall back to bare crossfades.
    """
    from mario.config import MarioConfig
    from mario.gen import generate, select_slots
    from mario.timebucket import bucket_for_now

    host = tmp_path / "host"
    host.mkdir()
    (host / "current-bucket").write_text("2026-11-14T12", encoding="utf-8")

    config = MarioConfig(host_dir=host, music_dir=tmp_path, model=tmp_path / "m.onnx")
    slots = select_slots("rss")
    assert slots is not None

    generate(
        config,
        dry_run=True,
        time_bucket="2026-11-14T12-30",
        slots=slots,
        write_marker=False,
        track_loader=lambda _p: [],
    )
    assert (host / "current-bucket").read_text(encoding="utf-8") == "2026-11-14T12"
    assert bucket_for_now(6).count("-") == 2  # sanity: buckets stay bucket-shaped


def test_republish_drops_the_superseded_content_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kokoro_env: tuple[Path, Path]
) -> None:
    """Otherwise every changed reading lingers until the bucket flips (up to 6 h).

    Nothing references the old file: both aliases move in the same call, and the
    reader keeps its inode open if it was mid-segment.
    """
    install_fake_kokoro(monkeypatch)
    model, voices = kokoro_env
    bucket = "2026-11-14T12"
    first = generate_weather_segment(
        "11111111", bucket, "18 grados", model, tmp_path, voices_path=voices
    )
    second = generate_weather_segment(
        "22222222", bucket, "19 grados", model, tmp_path, voices_path=voices
    )

    assert not first.exists(), "the superseded content file must be gone"
    assert second.exists()
    # Content files only: the alias is weather_<bucket>.wav and starts with digits.
    content_glob = "weather_" + "[0-9a-f]" * 8 + "_*.wav"
    remaining = sorted(p.name for p in tmp_path.glob(content_glob))
    assert remaining == [second.name]
    assert (tmp_path / "weather.wav").resolve() == second
    assert (tmp_path / f"weather_{bucket}.wav").resolve() == second


def test_republish_never_touches_another_bucket(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kokoro_env: tuple[Path, Path]
) -> None:
    install_fake_kokoro(monkeypatch)
    model, voices = kokoro_env
    other = generate_weather_segment(
        "33333333", "2026-11-14T06", "12 grados", model, tmp_path, voices_path=voices
    )
    generate_weather_segment(
        "44444444", "2026-11-14T12", "19 grados", model, tmp_path, voices_path=voices
    )
    assert other.exists(), "retention is prune's job, not the publisher's"


def test_cache_hit_does_not_delete_the_segment_it_reused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kokoro_env: tuple[Path, Path]
) -> None:
    """previous == target when the reading has not changed: keep the file."""
    install_fake_kokoro(monkeypatch)
    model, voices = kokoro_env
    bucket = "2026-11-14T12"
    first = generate_weather_segment(
        "aaaaaaaa", bucket, "18 grados", model, tmp_path, voices_path=voices
    )
    again = generate_weather_segment(
        "aaaaaaaa", bucket, "18 grados", model, tmp_path, voices_path=voices
    )
    assert first == again
    assert again.exists(), "a cache hit must not unlink its own content file"
    assert (tmp_path / "weather.wav").resolve() == again


def test_slot_runner_pins_the_unsafe_flags() -> None:
    """Both slot commands must carry --no-marker and --keep-buckets.

    --no-marker: a 30-minute news bucket in current-bucket would break every
    transition lookup. --keep-buckets: retention must not depend on the hourly
    full render existing.
    """
    script = Path(__file__).resolve().parents[1] / "scripts" / "mario-slot.sh"
    text = script.read_text(encoding="utf-8")
    slot_args = [line for line in text.splitlines() if "--slots" in line]
    assert slot_args, "the slot command lines must be discoverable"
    for line in slot_args:
        assert "--no-marker" in line
        assert "--keep-buckets 1" in line
