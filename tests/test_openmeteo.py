"""Tests for the Open-Meteo producer: WMO mapping, payload coercion, atomic write."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mario import openmeteo
from mario.weather import load_weather_snapshot


def _payload(code: int = 2, **overrides: object) -> dict[str, object]:
    current: dict[str, object] = {
        "time": "2026-11-14T14:15",
        "temperature_2m": 21.4,
        "weather_code": code,
        "wind_speed_10m": 12.6,
        "wind_direction_10m": 225.0,
    }
    current.update(overrides)
    return {"current": current}


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (0, "clear"),
        (1, "clear"),
        (2, "partly_cloudy"),
        (3, "overcast"),
        (45, "fog"),
        (51, "rain"),
        (61, "rain"),
        (71, "snow"),
        (80, "rain"),
        (85, "snow"),
        (95, "storm"),
        (99, "storm"),
    ],
)
def test_sky_for_known_codes(code: int, expected: str) -> None:
    assert openmeteo.sky_for(code) == expected


def test_sky_for_unknown_code_is_none() -> None:
    # 4..44 and 46..47 are not present-weather codes: refuse to guess a sky.
    assert openmeteo.sky_for(4) is None
    assert openmeteo.sky_for(46) is None


def test_snapshot_from_payload_happy_path() -> None:
    snapshot = openmeteo.snapshot_from_payload(_payload())
    assert snapshot is not None
    assert snapshot.temperature_c == pytest.approx(21.4)
    assert snapshot.wind_speed_kmh == pytest.approx(12.6)
    assert snapshot.wind_dir_deg == pytest.approx(225.0)
    assert snapshot.sky == "partly_cloudy"
    assert snapshot.observed_at == "2026-11-14T14:15"


def test_snapshot_from_payload_normalizes_direction() -> None:
    snapshot = openmeteo.snapshot_from_payload(_payload(wind_direction_10m=405.0))
    assert snapshot is not None
    assert snapshot.wind_dir_deg == pytest.approx(45.0)


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"current": "not-a-mapping"},
        {"current": {"temperature_2m": 1.0}},
        _payload(weather_code=4),
        _payload(temperature_2m="hot"),
        _payload(weather_code=None),
    ],
)
def test_snapshot_from_payload_rejects_bad_input(payload: dict[str, object]) -> None:
    assert openmeteo.snapshot_from_payload(payload) is None


def test_fetch_snapshot_returns_none_when_transport_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(openmeteo, "fetch_feed", lambda *_a, **_k: None)
    assert openmeteo.fetch_snapshot() is None


def test_fetch_snapshot_returns_none_on_garbage(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(openmeteo, "fetch_feed", lambda *_a, **_k: b"<html>nope</html>")
    assert openmeteo.fetch_snapshot() is None


def test_fetch_snapshot_parses_a_real_body(monkeypatch: pytest.MonkeyPatch) -> None:
    body = json.dumps(_payload()).encode()
    captured: dict[str, str] = {}

    def _fake_fetch(url: str, **_kwargs: object) -> bytes:
        captured["url"] = url
        return body

    monkeypatch.setattr(openmeteo, "fetch_feed", _fake_fetch)
    snapshot = openmeteo.fetch_snapshot(lat=-32.34, lon=-65.01)
    assert snapshot is not None and snapshot.sky == "partly_cloudy"
    assert "latitude=-32.34" in captured["url"]
    assert "longitude=-65.01" in captured["url"]
    assert "weather_code" in captured["url"]


def test_write_snapshot_is_readable_by_the_generator(tmp_path: Path) -> None:
    snapshot = openmeteo.snapshot_from_payload(_payload())
    assert snapshot is not None
    target = tmp_path / "weather.json"
    openmeteo.write_snapshot(snapshot, target)

    # The producer's output must satisfy the consumer's validator, unchanged.
    loaded = load_weather_snapshot(target)
    assert loaded == snapshot


def test_write_snapshot_leaves_no_tmp_and_replaces(tmp_path: Path) -> None:
    target = tmp_path / "weather.json"
    first = openmeteo.snapshot_from_payload(_payload(temperature_2m=10.0))
    second = openmeteo.snapshot_from_payload(_payload(temperature_2m=30.0))
    assert first is not None and second is not None

    openmeteo.write_snapshot(first, target)
    openmeteo.write_snapshot(second, target)

    assert [p.name for p in tmp_path.iterdir()] == ["weather.json"]
    assert load_weather_snapshot(target) == second


def test_write_snapshot_creates_parent_dir(tmp_path: Path) -> None:
    snapshot = openmeteo.snapshot_from_payload(_payload())
    assert snapshot is not None
    target = tmp_path / "nested" / "dir" / "weather.json"
    openmeteo.write_snapshot(snapshot, target)
    assert json.loads(target.read_text())["sky"] == "partly_cloudy"
