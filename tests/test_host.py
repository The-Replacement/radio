"""Tests for Mario — the host voice generator."""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import UTC
from pathlib import Path
from types import SimpleNamespace

import pytest

from mario.render import cache_filename, generate_segment, render_wav
from mario.text import build_text
from mario.timebucket import quantize_bucket, sanitize_bucket
from mario.tracks import TrackMeta, load_tracks
from mario.vibe import SPEED_MAX, SPEED_MIN, lane_mood, vibe_for


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


def test_text_determinism_and_persona() -> None:
    a = TrackMeta(seed=1, lane="motorik", form="A", preset="p", pcm_sha256="h1", path="x", duration_seconds=10.0)
    b = TrackMeta(seed=2, lane="kosmische", form="B", preset="p", pcm_sha256="h2", path="y", duration_seconds=10.0)
    t1 = build_text(a, b, "2026-11-10T14")
    t2 = build_text(a, b, "2026-11-10T14")
    assert t1 == t2
    # DJ claims the act and cues the incoming track/vibe.
    assert "I'm" in t1
    assert "kosmische" in t1 and "B" in t1
    assert "..." in t1  # spacious pacing markers


def test_text_reflects_lane_contrast() -> None:
    motorik = TrackMeta(seed=1, lane="motorik", form="A", preset="p", pcm_sha256="h1", path="x", duration_seconds=10.0)
    ambient = TrackMeta(seed=2, lane="ambient", form="B", preset="p", pcm_sha256="h2", path="y", duration_seconds=10.0)
    text = build_text(motorik, ambient, "2026-11-10T14")
    # Shift from driving to hush should be named softly.
    assert "settle" in text.lower() or "dim" in text.lower() or "quiet" in text.lower()


def test_vibe_speed_deterministic_and_in_bounds() -> None:
    a = TrackMeta(seed=11, lane="motorik", form="A", preset="p", pcm_sha256="h1", path="x", duration_seconds=10.0)
    b = TrackMeta(seed=22, lane="ambient", form="B", preset="p", pcm_sha256="h2", path="y", duration_seconds=10.0)
    v1 = vibe_for(a, b, "2026-11-10T14")
    v2 = vibe_for(a, b, "2026-11-10T14")
    assert v1 == v2
    assert SPEED_MIN <= v1.speed <= SPEED_MAX


def test_lane_to_mood_and_pacing_mapping() -> None:
    assert lane_mood("motorik") == "pulse"
    assert lane_mood("kosmische") == "drift"
    assert lane_mood("ambient") == "hush"
    motorik = TrackMeta(seed=1, lane="motorik", form="A", preset="p", pcm_sha256="h1", path="x", duration_seconds=10.0)
    kosmische = TrackMeta(seed=2, lane="kosmische", form="A", preset="p", pcm_sha256="h2", path="y", duration_seconds=10.0)
    ambient = TrackMeta(seed=3, lane="ambient", form="A", preset="p", pcm_sha256="h3", path="z", duration_seconds=10.0)
    v_motorik = vibe_for(motorik, motorik, "2026-11-10T14")
    v_drift = vibe_for(motorik, kosmische, "2026-11-10T14")
    v_hush = vibe_for(motorik, ambient, "2026-11-10T14")
    assert v_hush.speed < v_motorik.speed
    assert v_drift.speed < v_motorik.speed
    assert v_hush.speed <= v_drift.speed + 0.03  # both floaty, ambient can be slowest


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
    p1 = generate_segment(1, 2, "2026-11-10T14", "hi", model, host_dir, piper_bin="piper")
    assert p1.is_file()
    assert calls["n"] == 1
    p2 = generate_segment(1, 2, "2026-11-10T14", "hi", model, host_dir, piper_bin="piper")
    assert p2 == p1
    assert calls["n"] == 1


def test_kokoro_path_receives_speed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    seen: dict[str, object] = {}

    class FakeKokoro:
        def __init__(self, model: str, voices: str) -> None:
            seen["model"] = model
            seen["voices"] = voices

        def create(self, text: str, voice: str, speed: float, lang: str):  # type: ignore[no-untyped-def]
            seen["text"] = text
            seen["voice"] = voice
            seen["speed"] = speed
            seen["lang"] = lang
            return [0.0, 0.0], 24000

    def fake_write(path: str, samples: object, sample_rate: int, format: str = "WAV") -> None:
        Path(path).write_bytes(b"RIFF")

    monkeypatch.setitem(__import__("sys").modules, "kokoro_onnx", SimpleNamespace(Kokoro=FakeKokoro))
    monkeypatch.setitem(__import__("sys").modules, "soundfile", SimpleNamespace(write=fake_write))

    model = tmp_path / "kokoro.onnx"
    voices = tmp_path / "voices.bin"
    model.write_bytes(b"x")
    voices.write_bytes(b"v")
    out = tmp_path / "seg.wav"
    render_wav(
        "hello there",
        model,
        out,
        backend="kokoro",
        voices_path=voices,
        voice="am_adam",
        speed=0.88,
        lang="en-us",
    )
    assert out.is_file()
    assert seen["speed"] == 0.88
    assert seen["voice"] == "am_adam"
    assert seen["lang"] == "en-us"


def test_missing_model_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        render_wav("hi", tmp_path / "nope.onnx", tmp_path / "out.wav")


def test_no_network_imports() -> None:
    import mario.gen as g
    import mario.liners as l
    import mario.render as r
    import mario.rss as s
    import mario.tracks as t
    import mario.vibe as v
    import mario.weather as w

    src = "\n".join(__import__("inspect").getsource(m) for m in (g, l, r, s, t, v, w))
    for forbidden in ("requests", "urllib", "httpx", "socket"):
        assert forbidden not in src, f"forbidden network client referenced: {forbidden}"


def test_only_feedfetch_touches_network() -> None:
    import mario

    package_dir = Path(mario.__file__).resolve().parent
    offenders: list[str] = []
    for path in sorted(package_dir.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        for forbidden in ("requests", "urllib", "httpx", "socket"):
            if forbidden in text and path.name != "feedfetch.py":
                offenders.append(f"{path.name}: {forbidden}")
    assert not offenders, f"only feedfetch.py may reference a network client: {offenders}"


def test_host_determinism(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
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


def test_preset_resolution_finds_repo_presets() -> None:
    from mario.presets import preset_search_paths, resolve_preset_path

    for name in ("liners.yaml", "weather.yaml", "rss.yaml"):
        resolved = resolve_preset_path(name)
        assert resolved is not None, f"{name} not resolved from source checkout"
        assert resolved.is_file()
        assert resolved.read_text(encoding="utf-8").strip()

    paths = preset_search_paths("rss.yaml")
    assert len(paths) == len({str(p) for p in paths})
    assert all(p.name == "rss.yaml" for p in paths)


def test_preset_resolution_env_override_wins(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from mario.presets import PRESETS_DIR_ENV, resolve_preset_path

    override = tmp_path / "custom-presets"
    override.mkdir()
    marker = override / "rss.yaml"
    marker.write_text("template_version: mario/rss/9\nintro: 'Hi {title}.'\nfallback: 'x'\n")

    monkeypatch.setenv(PRESETS_DIR_ENV, str(override))
    assert resolve_preset_path("rss.yaml") == marker


def test_preset_resolution_container_layout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The container copies presets to /app/presets; the installed package is not the source tree."""
    from mario import presets

    container = tmp_path / "app" / "presets"
    container.mkdir(parents=True)
    (container / "weather.yaml").write_text("template: 'x {sky}'\nfallback: 'y'\n")
    monkeypatch.delenv(presets.PRESETS_DIR_ENV, raising=False)
    monkeypatch.setattr(presets, "_CONTAINER_PRESETS_DIR", container)

    resolved = presets.resolve_preset_path("weather.yaml")
    assert resolved == container / "weather.yaml"


def test_preset_resolution_missing_raises_with_searched_paths(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from mario import presets

    monkeypatch.delenv(presets.PRESETS_DIR_ENV, raising=False)
    monkeypatch.setattr(presets, "_CONTAINER_PRESETS_DIR", tmp_path / "nope")
    with pytest.raises(FileNotFoundError) as excinfo:
        presets.require_preset_path("does-not-exist.yaml")
    message = str(excinfo.value)
    assert "does-not-exist.yaml" in message
    assert "searched" in message
