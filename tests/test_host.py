"""Tests for Mario — the host voice generator."""

from __future__ import annotations

import json
import subprocess
from datetime import UTC
from pathlib import Path

import pytest

from mario.render import cache_filename, generate_segment, render_wav
from mario.text import build_text
from mario.timebucket import quantize_bucket, sanitize_bucket
from mario.tracks import TrackMeta, load_tracks


def _sidecar(music_dir: Path, seed: int, lane: str = "motorik", form: str = "A") -> None:
    payload = {
        "schema": "indiegen/sidecar/1",
        "seed": seed,
        "lane": lane,
        "preset": "motorik",
        "pcm_sha256": f"hash{seed}",
        "plan": {"form": form, "techniques": [], "sections": [], "director_log": []},
        "measurements": {"duration_seconds": 10.0, "integrated_lufs": -16.0, "true_peak_dbtp": -1.5, "loudness_range_lu": 5.0},
    }
    (music_dir / f"track{seed}.json").write_text(json.dumps(payload), encoding="utf-8")


def test_cache_filename_format() -> None:
    name = cache_filename(1, 2, "2026-11-10T14")
    assert name == "1_2_2026-11-10T14.wav"


def test_sanitize_bucket() -> None:
    assert sanitize_bucket("2026-11-10T14:30") == "2026-11-10T14-30"


def test_time_bucket_quantization() -> None:
    from datetime import datetime

    dt = datetime(2026, 11, 10, 14, 37, tzinfo=UTC)
    assert quantize_bucket(dt, 1) == "2026-11-10T14"
    assert quantize_bucket(dt, 24) == "2026-11-10"


def test_text_determinism() -> None:
    a = TrackMeta(seed=1, lane="motorik", form="A", preset="p", pcm_sha256="h1", path="x", duration_seconds=10.0)
    b = TrackMeta(seed=2, lane="kosmische", form="B", preset="p", pcm_sha256="h2", path="y", duration_seconds=10.0)
    t1 = build_text(a, b, "2026-11-10T14")
    t2 = build_text(a, b, "2026-11-10T14")
    assert t1 == t2
    assert "motorik" in t1 and "kosmische" in t1


def test_load_tracks_from_sidecars(tmp_path: Path) -> None:
    _sidecar(tmp_path, 10, "motorik", "A")
    _sidecar(tmp_path, 20, "ambient", "B")
    tracks = load_tracks(tmp_path)
    assert len(tracks) == 2
    seeds = {t.seed for t in tracks}
    assert seeds == {10, 20}


def test_render_noise_scales(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    captured: dict[str, list[str]] = {}

    def fake_run(cmd: list[str], **kwargs: object) -> object:
        captured["cmd"] = list(cmd)
        out = Path(cmd[cmd.index("-f") + 1])
        out.write_bytes(b"RIFF")
        class R:
            returncode = 0
            stdout = b""
            stderr = b""
        return R()

    monkeypatch.setattr(subprocess, "run", fake_run)
    model = tmp_path / "m.onnx"
    model.write_bytes(b"x")
    out = tmp_path / "seg.wav"
    render_wav("hello", model, out)
    cmd = captured["cmd"]
    assert "--noise-scale" in cmd
    idx = cmd.index("--noise-scale")
    assert cmd[idx + 1] == "0"
    assert "--noise-w-scale" in cmd
    idx2 = cmd.index("--noise-w-scale")
    assert cmd[idx2 + 1] == "0"


def test_cache_skip(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls = {"n": 0}

    def fake_run(cmd: list[str], **kwargs: object) -> object:
        calls["n"] += 1
        out = Path(cmd[cmd.index("-f") + 1])
        out.write_bytes(b"RIFF")
        class R:
            returncode = 0
            stdout = b""
            stderr = b""
        return R()

    monkeypatch.setattr(subprocess, "run", fake_run)
    model = tmp_path / "m.onnx"
    model.write_bytes(b"x")
    host_dir = tmp_path / "host"
    host_dir.mkdir()
    # First render creates file.
    p1 = generate_segment(1, 2, "2026-11-10T14", "hi", model, host_dir, piper_bin="piper")
    assert p1.is_file()
    assert calls["n"] == 1
    # Second run skips (no piper call).
    p2 = generate_segment(1, 2, "2026-11-10T14", "hi", model, host_dir, piper_bin="piper")
    assert p2 == p1
    assert calls["n"] == 1


def test_missing_model_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        render_wav("hi", tmp_path / "nope.onnx", tmp_path / "out.wav")


def test_no_network_imports() -> None:
    import mario.gen as g
    import mario.render as r
    import mario.tracks as t
    src = "\n".join(__import__("inspect").getsource(m) for m in (g, r, t))
    for forbidden in ("requests", "urllib", "httpx", "socket"):
        assert forbidden not in src, f"forbidden network client referenced: {forbidden}"


def test_host_determinism(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import hashlib

    music = tmp_path / "music"
    music.mkdir()
    _sidecar(music, 1, "motorik", "A")
    _sidecar(music, 2, "kosmische", "B")

    def fake_run(cmd: list[str], **kwargs: object) -> object:
        out = Path(cmd[cmd.index("-f") + 1])
        out.write_bytes(b"DETERMINISTIC-WAV-BYTES")
        class R:
            returncode = 0
            stdout = b""
            stderr = b""
        return R()

    monkeypatch.setattr(subprocess, "run", fake_run)
    model = tmp_path / "m.onnx"
    model.write_bytes(b"x")

    from mario.config import MarioConfig
    from mario.gen import generate

    cfg1 = MarioConfig(music_dir=music, host_dir=tmp_path / "host1", model=model, time_bucket_hours=1)
    cfg2 = MarioConfig(music_dir=music, host_dir=tmp_path / "host2", model=model, time_bucket_hours=1)
    paths1 = generate(cfg1, time_bucket="2026-11-10T14", force=True)
    paths2 = generate(cfg2, time_bucket="2026-11-10T14", force=True)

    assert sorted(p.name for p in paths1) == sorted(p.name for p in paths2)
    hashes1 = sorted(hashlib.sha256(p.read_bytes()).hexdigest() for p in paths1)
    hashes2 = sorted(hashlib.sha256(p.read_bytes()).hexdigest() for p in paths2)
    assert hashes1 == hashes2
