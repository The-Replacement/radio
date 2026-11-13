from __future__ import annotations

import json
from pathlib import Path

import pytest

from mario.config import MarioConfig
from mario.gen import generate
from mario.liners import liner_cache_filename, load_liners, select_liner


def _sidecar(music_dir: Path, seed: int, lane: str = "motorik", form: str = "A") -> None:
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
    (music_dir / f"track{seed}.json").write_text(json.dumps(payload), encoding="utf-8")


def test_liners_preset_pool_has_minimum_size() -> None:
    pool = load_liners(Path("presets/liners.yaml"))
    assert pool.template_version
    assert 5 <= len(pool.liners) <= 10
    assert all(l.ident_id and l.text for l in pool.liners)


def test_liner_selection_deterministic_by_seed() -> None:
    pool = load_liners(Path("presets/liners.yaml"))
    a = select_liner(pool, 42)
    b = select_liner(pool, 42)
    c = select_liner(pool, 43)
    assert a == b
    assert a.ident_id == b.ident_id
    # same seed -> same ident always
    assert select_liner(pool, 42).text == a.text
    # likely different for different seed (not required but sanity when pool>1)
    assert c in pool.liners


def test_liner_cache_filename_includes_ident_seed_bucket() -> None:
    name = liner_cache_filename("ident-01", 123, "2026-11-10T14")
    assert name == "liner_ident-01_123_2026-11-10T14.wav"


def test_generate_renders_liners_for_tracks(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    music = tmp_path / "music"
    music.mkdir()
    _sidecar(music, 1, "motorik", "A")
    _sidecar(music, 2, "kosmische", "B")

    from tests.conftest import install_fake_kokoro
    install_fake_kokoro(monkeypatch, marker=b"RIFF-LINER")
    model = tmp_path / "kokoro.onnx"
    voices = tmp_path / "voices.bin"
    model.write_bytes(b"m"); voices.write_bytes(b"v")

    cfg = MarioConfig(
        music_dir=music,
        host_dir=tmp_path / "host",
        model=model,
        voices_path=voices,
        liners_preset=Path("presets/liners.yaml"),
    )
    paths = generate(cfg, time_bucket="2026-11-10T14", force=True)
    names = {p.name for p in paths}
    pool = load_liners(Path("presets/liners.yaml"))
    # one liner per unique track seed
    for seed in (1, 2):
        ident = select_liner(pool, seed)
        assert liner_cache_filename(ident.ident_id, seed, "2026-11-10T14") in names
    liner_files = [p for p in paths if p.name.startswith("liner_")]
    assert len(liner_files) == 2
    assert all(p.is_file() and p.stat().st_size > 0 for p in liner_files)
