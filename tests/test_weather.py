from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from mario.config import MarioConfig
from mario.gen import generate
from mario.weather import (
    FALLBACK_WEATHER_HASH,
    build_weather_text,
    compass_16,
    load_weather_preset,
    load_weather_snapshot,
    weather_alias_filename,
    weather_cache_filename,
    weather_snapshot_hash,
)

PRESET_PATH = Path("presets/weather.yaml")


def _snapshot_data(**overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "temperature_c": 14.0,
        "wind_dir_deg": 270.0,
        "wind_speed_kmh": 12.0,
        "sky": "clear",
        "observed_at": "2026-11-10T14:00:00Z",
    }
    data.update(overrides)
    return data


def _write_snapshot(path: Path, data: dict[str, object]) -> Path:
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_weather_preset_loads() -> None:
    preset = load_weather_preset(PRESET_PATH)
    assert preset.template_version
    assert preset.template
    assert preset.fallback


def test_compass_16_deterministic() -> None:
    assert compass_16(0) == "north"
    assert compass_16(360) == "north"
    assert compass_16(270) == "west"
    assert compass_16(45) == "northeast"
    assert compass_16(22) == "north-northeast"


def test_valid_snapshot_parse_and_text() -> None:
    preset = load_weather_preset(PRESET_PATH)
    snap = load_weather_snapshot(Path("/nonexistent"))  # ensure None path ok separately
    assert snap is None
    # valid
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        p = _write_snapshot(Path(td) / "w.json", _snapshot_data(temperature_c=14, wind_dir_deg=270, wind_speed_kmh=12, sky="clear"))
        snap = load_weather_snapshot(p)
        assert snap is not None
        assert snap.temperature_c == 14.0
        assert snap.wind_dir_deg == 270.0
        assert snap.sky == "clear"
        text, h = build_weather_text(snap, preset)
        assert h == weather_snapshot_hash(snap)
        assert "14 degrees Celsius" in text
        assert "west" in text  # 270 -> west
        assert "12 kilometers per hour" in text
        assert "clear" in text


def test_canonical_hash_stable_across_formatting() -> None:
    import tempfile

    data = _snapshot_data(temperature_c=14.0, wind_dir_deg=270.0, wind_speed_kmh=12.0, sky="clear")
    with tempfile.TemporaryDirectory() as td:
        p1 = Path(td) / "a.json"
        p2 = Path(td) / "b.json"
        p1.write_text(json.dumps(data, indent=2, sort_keys=False), encoding="utf-8")
        # different key order and whitespace
        p2.write_text('{\n  "sky": "clear", "wind_speed_kmh": 12.0, "observed_at": "2026-11-10T14:00:00Z", "wind_dir_deg": 270.0, "temperature_c": 14.0\n}\n', encoding="utf-8")
        s1 = load_weather_snapshot(p1)
        s2 = load_weather_snapshot(p2)
        assert s1 is not None and s2 is not None
        assert weather_snapshot_hash(s1) == weather_snapshot_hash(s2)
        preset = load_weather_preset(PRESET_PATH)
        t1, h1 = build_weather_text(s1, preset)
        t2, h2 = build_weather_text(s2, preset)
        assert h1 == h2
        assert t1 == t2


def test_observed_at_does_not_change_hash() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        p1 = _write_snapshot(Path(td) / "a.json", _snapshot_data(observed_at="2026-11-10T14:00:00Z"))
        p2 = _write_snapshot(Path(td) / "b.json", _snapshot_data(observed_at="2026-11-10T15:00:00Z"))
        s1 = load_weather_snapshot(p1)
        s2 = load_weather_snapshot(p2)
        assert s1 is not None and s2 is not None
        assert weather_snapshot_hash(s1) == weather_snapshot_hash(s2)


def test_fallback_on_missing_and_invalid(tmp_path: Path) -> None:
    preset = load_weather_preset(PRESET_PATH)
    # missing
    snap = load_weather_snapshot(tmp_path / "missing.json")
    assert snap is None
    text, h = build_weather_text(snap, preset)
    assert text == preset.fallback
    assert h == FALLBACK_WEATHER_HASH
    # invalid json
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert load_weather_snapshot(bad) is None
    # invalid schema/sky
    bad2 = tmp_path / "bad2.json"
    bad2.write_text(json.dumps({"temperature_c": 1, "wind_dir_deg": 1, "wind_speed_kmh": 1, "sky": "volcanic"}), encoding="utf-8")
    assert load_weather_snapshot(bad2) is None
    # missing fields
    bad3 = tmp_path / "bad3.json"
    bad3.write_text(json.dumps({"temperature_c": 1}), encoding="utf-8")
    assert load_weather_snapshot(bad3) is None


def test_weather_cache_and_alias_filenames() -> None:
    h = "abcdef1234567890"
    assert weather_cache_filename(h, "2026-11-10T14") == "weather_abcdef12_2026-11-10T14.wav"
    assert weather_alias_filename("2026-11-10T14") == "weather_2026-11-10T14.wav"


from collections.abc import Callable


def _fake_piper_run_factory(marker: bytes = b"RIFF-WEATHER") -> Callable[..., object]:
    def fake_run(cmd: list[str], **kwargs: object) -> object:
        out = Path(cmd[cmd.index("-f") + 1])
        out.write_bytes(marker)

        class R:
            returncode = 0
            stdout = b""
            stderr = b""

        return R()

    return fake_run


def test_generate_weather_only_empty_library(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(subprocess, "run", _fake_piper_run_factory())
    music = tmp_path / "music"
    music.mkdir()
    weather_file = _write_snapshot(tmp_path / "weather.json", _snapshot_data())
    model = tmp_path / "m.onnx"
    model.write_bytes(b"x")
    host = tmp_path / "host"
    cfg = MarioConfig(music_dir=music, host_dir=host, model=model, weather_file=weather_file)
    paths = generate(cfg, time_bucket="2026-11-10T14", force=True)
    assert len(paths) == 1
    assert paths[0].is_file()
    assert paths[0].name.startswith("weather_")
    assert "2026-11-10T14" in paths[0].name
    alias = host / "weather_2026-11-10T14.wav"
    assert alias.exists()
    assert (host / "current-bucket").read_text(encoding="utf-8") == "2026-11-10T14"


def test_empty_library_no_weather_returns_empty(tmp_path: Path) -> None:
    music = tmp_path / "music"
    music.mkdir()
    model = tmp_path / "m.onnx"
    model.write_bytes(b"x")
    cfg = MarioConfig(music_dir=music, host_dir=tmp_path / "host", model=model)
    paths = generate(cfg, time_bucket="2026-11-10T14")
    assert paths == []


def test_weather_fallback_missing_snapshot(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(subprocess, "run", _fake_piper_run_factory())
    music = tmp_path / "music"
    music.mkdir()
    model = tmp_path / "m.onnx"
    model.write_bytes(b"x")
    host = tmp_path / "host"
    # configured but file does not exist -> fallback, no raise
    cfg = MarioConfig(music_dir=music, host_dir=host, model=model, weather_file=tmp_path / "missing.json")
    paths = generate(cfg, time_bucket="2026-11-10T14", force=True)
    assert len(paths) == 1
    assert FALLBACK_WEATHER_HASH[:8] in paths[0].name
    assert (host / "weather_2026-11-10T14.wav").exists()


def test_weather_fallback_does_not_block_liners(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(subprocess, "run", _fake_piper_run_factory())
    music = tmp_path / "music"
    music.mkdir()
    # one track sidecar
    payload = {
        "schema": "indiegen/sidecar/1",
        "seed": 1,
        "lane": "motorik",
        "preset": "motorik",
        "pcm_sha256": "hash1",
        "plan": {"form": "A", "techniques": [], "sections": [], "director_log": []},
        "measurements": {"duration_seconds": 10.0, "integrated_lufs": -16.0, "true_peak_dbtp": -1.5, "loudness_range_lu": 5.0},
    }
    (music / "track1.json").write_text(json.dumps(payload), encoding="utf-8")
    model = tmp_path / "m.onnx"
    model.write_bytes(b"x")
    host = tmp_path / "host"
    cfg = MarioConfig(
        music_dir=music,
        host_dir=host,
        model=model,
        weather_file=tmp_path / "missing.json",
        liners_preset=Path("presets/liners.yaml"),
    )
    paths = generate(cfg, time_bucket="2026-11-10T14", force=True)
    names = {p.name for p in paths}
    assert any(n.startswith("liner_") for n in names)
    assert any(n.startswith("weather_") and FALLBACK_WEATHER_HASH[:8] in n for n in names)


def test_weather_one_per_bucket(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(subprocess, "run", _fake_piper_run_factory())
    music = tmp_path / "music"
    music.mkdir()
    # two tracks -> transitions+liners but only one weather
    for seed in (1, 2):
        payload = {
            "schema": "indiegen/sidecar/1",
            "seed": seed,
            "lane": "motorik",
            "preset": "motorik",
            "pcm_sha256": f"hash{seed}",
            "plan": {"form": "A", "techniques": [], "sections": [], "director_log": []},
            "measurements": {"duration_seconds": 10.0, "integrated_lufs": -16.0, "true_peak_dbtp": -1.5, "loudness_range_lu": 5.0},
        }
        (music / f"track{seed}.json").write_text(json.dumps(payload), encoding="utf-8")
    weather_file = _write_snapshot(tmp_path / "weather.json", _snapshot_data())
    model = tmp_path / "m.onnx"
    model.write_bytes(b"x")
    cfg = MarioConfig(music_dir=music, host_dir=tmp_path / "host", model=model, weather_file=weather_file, liners_preset=Path("presets/liners.yaml"))
    paths = generate(cfg, time_bucket="2026-11-10T14", force=True)
    # only the cache file named weather_<hash8>_bucket.wav (alias weather_<bucket>.wav not returned)
    weather_cache_paths = [
        p for p in paths if p.name.startswith("weather_") and p.name.count("_") >= 2
    ]
    assert len(weather_cache_paths) == 1
    assert weather_cache_paths[0].name.endswith("_2026-11-10T14.wav")


def test_weather_dry_run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
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
    music = tmp_path / "music"
    music.mkdir()
    weather_file = _write_snapshot(tmp_path / "weather.json", _snapshot_data())
    model = tmp_path / "m.onnx"
    model.write_bytes(b"x")
    host = tmp_path / "host"
    cfg = MarioConfig(music_dir=music, host_dir=host, model=model, weather_file=weather_file)
    paths = generate(cfg, time_bucket="2026-11-10T14", dry_run=True)
    assert len(paths) == 1
    assert paths[0].name.startswith("weather_")
    assert not paths[0].exists()
    assert calls["n"] == 0


def test_weather_determinism_across_runs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(subprocess, "run", _fake_piper_run_factory(b"DETERMINISTIC-WEATHER"))
    music = tmp_path / "music"
    music.mkdir()
    weather_file = _write_snapshot(tmp_path / "weather.json", _snapshot_data())
    model = tmp_path / "m.onnx"
    model.write_bytes(b"x")
    cfg1 = MarioConfig(music_dir=music, host_dir=tmp_path / "h1", model=model, weather_file=weather_file)
    cfg2 = MarioConfig(music_dir=music, host_dir=tmp_path / "h2", model=model, weather_file=weather_file)
    p1 = generate(cfg1, time_bucket="2026-11-10T14", force=True)
    p2 = generate(cfg2, time_bucket="2026-11-10T14", force=True)
    assert [p.name for p in p1] == [p.name for p in p2]
    assert hashlib.sha256(p1[0].read_bytes()).hexdigest() == hashlib.sha256(p2[0].read_bytes()).hexdigest()


def test_config_env_weather_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    wf = tmp_path / "w.json"
    wf.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("MARIO_WEATHER_FILE", str(wf))
    cfg = MarioConfig.from_env()
    assert cfg.weather_file == wf
    monkeypatch.delenv("MARIO_WEATHER_FILE", raising=False)
    cfg2 = MarioConfig.from_env()
    assert cfg2.weather_file is None
