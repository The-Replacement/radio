"""Tests for retention of Mario's host segment pool."""

from __future__ import annotations

from pathlib import Path

from mario.prune import parse_bucket, prune_host_dir


def test_parse_bucket() -> None:
    assert parse_bucket("1_2_2026-11-10T14.wav") == "2026-11-10T14"
    assert parse_bucket("weather_abcd_2026-11-10.wav") == "2026-11-10"
    assert parse_bucket("current-bucket") is None


def test_prune_noop_inside_window(tmp_path: Path) -> None:
    host = tmp_path / "host"
    host.mkdir()
    for b in ("2026-11-10T12", "2026-11-10T13"):
        (host / f"1_2_{b}.wav").write_bytes(b"")
    deleted = prune_host_dir(host, keep_buckets=10)
    assert deleted == []
    assert len(list(host.glob("*.wav"))) == 2


def test_prune_deletes_stale_buckets(tmp_path: Path) -> None:
    host = tmp_path / "host"
    host.mkdir()
    for b in ("2026-11-10T10", "2026-11-10T11", "2026-11-10T12", "2026-11-10T13"):
        (host / f"1_2_{b}.wav").write_bytes(b"")
    deleted = prune_host_dir(host, keep_buckets=2)
    assert len(deleted) == 2
    remaining = sorted(p.name for p in host.glob("*.wav"))
    assert remaining == ["1_2_2026-11-10T12.wav", "1_2_2026-11-10T13.wav"]


def test_prune_protects_current_bucket(tmp_path: Path) -> None:
    host = tmp_path / "host"
    host.mkdir()
    (host / "current-bucket").write_text("2026-11-10T10", encoding="utf-8")
    for b in ("2026-11-10T10", "2026-11-10T11", "2026-11-10T12"):
        (host / f"1_2_{b}.wav").write_bytes(b"")
    deleted = prune_host_dir(host, keep_buckets=1)
    assert len(deleted) == 1
    assert (host / "1_2_2026-11-10T10.wav").exists()


def test_prune_never_deletes_newer_than_cutoff(tmp_path: Path) -> None:
    host = tmp_path / "host"
    host.mkdir()
    for h in range(10, 16):
        b = f"2026-11-10T{h:02d}"
        (host / f"1_2_{b}.wav").write_bytes(b"")
    prune_host_dir(host, keep_buckets=2)
    remaining = {parse_bucket(p.name) for p in host.glob("*.wav")}
    assert all(b is not None and b >= "2026-11-10T14" for b in remaining)


def test_prune_dry_run_does_not_delete(tmp_path: Path) -> None:
    host = tmp_path / "host"
    host.mkdir()
    for b in ("2026-11-10T10", "2026-11-10T11", "2026-11-10T12"):
        (host / f"1_2_{b}.wav").write_bytes(b"")
    deleted = prune_host_dir(host, keep_buckets=1, dry_run=True)
    assert len(deleted) == 2
    assert len(list(host.glob("*.wav"))) == 3


def test_prune_protects_inputs(tmp_path: Path) -> None:
    host = tmp_path / "host"
    host.mkdir()
    (host / "weather.json").write_text("{}", encoding="utf-8")
    (host / "rss-cache").mkdir()
    (host / "rss-cache" / "x").write_text("x", encoding="utf-8")
    for b in ("2026-11-10T10", "2026-11-10T11"):
        (host / f"1_2_{b}.wav").write_bytes(b"")
    prune_host_dir(host, keep_buckets=1)
    assert (host / "weather.json").exists()
    assert (host / "rss-cache" / "x").exists()


def test_prune_invalid_keep_buckets_raises(tmp_path: Path) -> None:
    host = tmp_path / "host"
    host.mkdir()
    import pytest

    with pytest.raises(ValueError):
        prune_host_dir(host, keep_buckets=0)