"""T1: metadata-dir loading decoupled from audio co-location (split layout prep)."""

from __future__ import annotations

import json
from pathlib import Path

from mario.config import MarioConfig
from mario.tracks import load_tracks


def _write_sidecar(meta_dir: Path, seed: int, lane: str = "motorik", form: str = "A") -> None:
    payload = {
        "schema": "indiegen/sidecar/1",
        "seed": seed,
        "lane": lane,
        "preset": "motorik",
        "pcm_sha256": f"hash{seed}",
        "plan": {"form": form, "techniques": [], "sections": [], "director_log": []},
        "measurements": {
            "duration_seconds": 10.0,
            "integrated_lufs": -16.0,
            "true_peak_dbtp": -1.5,
            "loudness_range_lu": 5.0,
        },
    }
    (meta_dir / f"track{seed}.json").write_text(json.dumps(payload), encoding="utf-8")


def test_load_tracks_from_sidecars_without_audio_files(tmp_path: Path) -> None:
    meta = tmp_path / "meta"
    meta.mkdir()
    _write_sidecar(meta, 10, "motorik", "A")
    _write_sidecar(meta, 20, "ambient", "B")
    # No .flac files present (audio lives in a separate scan root in split layout).
    assert list(meta.glob("*.flac")) == []

    tracks = load_tracks(meta)
    assert len(tracks) == 2
    assert {t.seed for t in tracks} == {10, 20}
    assert all(t.pcm_sha256 for t in tracks)


def test_load_tracks_from_catalog_without_audio_files(tmp_path: Path) -> None:
    meta = tmp_path / "meta"
    meta.mkdir()
    catalog = {
        "schema": "indiegen/catalog/1",
        "tracks": {
            "hash7": {
                "seed": 7,
                "lane": "kosmische",
                "form": "B",
                "preset": "motorik",
                "path": "track7.flac",
                "duration_seconds": 10.0,
                "alias": "Alias7",
                "album": "Album7",
                "title": "Title7",
            }
        },
    }
    (meta / "catalog.json").write_text(json.dumps(catalog), encoding="utf-8")
    # No sidecar and no .flac files present: catalog-only metadata in split layout.
    assert list(meta.glob("*.flac")) == []
    assert list(meta.glob("track7.json")) == []

    tracks = load_tracks(meta)
    assert len(tracks) == 1
    t = tracks[0]
    assert t.seed == 7
    assert t.lane == "kosmische"
    assert t.alias == "Alias7"


def test_config_meta_dir_defaults_and_env(monkeypatch: object, tmp_path: Path) -> None:
    monkeypatch.delenv("MARIO_META_DIR", raising=False)
    monkeypatch.setenv("MARIO_MUSIC_DIR", str(tmp_path / "lib"))
    cfg = MarioConfig.from_env()
    assert cfg.music_dir == tmp_path / "lib"
    assert cfg.meta_dir == tmp_path / "lib"
    assert cfg.metadata_dir == tmp_path / "lib"

    # MARIO_META_DIR takes precedence for metadata loading (split layout prep).
    monkeypatch.setenv("MARIO_META_DIR", str(tmp_path / "meta"))
    cfg2 = MarioConfig.from_env()
    assert cfg2.meta_dir == tmp_path / "meta"
    assert cfg2.metadata_dir == tmp_path / "meta"
    # MARIO_MUSIC_DIR still respected as legacy field when explicitly set.
    assert cfg2.music_dir == tmp_path / "lib"

    # When only MARIO_META_DIR is set, both resolve to meta for compatibility.
    monkeypatch.delenv("MARIO_MUSIC_DIR", raising=False)
    cfg3 = MarioConfig.from_env()
    assert cfg3.meta_dir == tmp_path / "meta"
    assert cfg3.music_dir == tmp_path / "meta"
    assert cfg3.metadata_dir == tmp_path / "meta"
