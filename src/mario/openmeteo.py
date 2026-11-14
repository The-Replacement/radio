"""The second (and last) network egress of Mario: Open-Meteo -> WeatherSnapshot.

Mirrors ``feedfetch``: one HTTPS GET, bounded bytes, a timeout, and ``None`` on
any failure. A dead network must never produce a half-valid snapshot — the
segment falls back to the preset's written fallback line instead.

The snapshot file is the contract with ``mario generate --weather-file``: the
keys are exactly the ones ``load_weather_snapshot`` validates.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .feedfetch import fetch_feed
from .weather import Sky, WeatherSnapshot, snapshot_from_mapping

# Villa de Merlo, San Luis — the Sierras de los Comechingones the preset names.
DEFAULT_LAT: float = -32.34
DEFAULT_LON: float = -65.01

_API_URL: str = "https://api.open-meteo.com/v1/forecast"
_CURRENT_FIELDS: str = "temperature_2m,weather_code,wind_speed_10m,wind_direction_10m"
_MAX_BYTES: int = 64 * 1024

# WMO 4677 present-weather code -> the sky vocabulary the preset can speak
# (weather.py:_VALID_SKY). Unknown codes are NOT guessed: a missing snapshot
# gives the honest fallback, a wrong sky gives wrong speech on air.
_WMO_SKY: dict[int, Sky] = {
    0: "clear",
    1: "clear",  # mainly clear
    2: "partly_cloudy",
    3: "overcast",
    45: "fog",
    48: "fog",
    51: "rain",  # drizzle
    53: "rain",
    55: "rain",
    56: "rain",  # freezing drizzle
    57: "rain",
    61: "rain",
    63: "rain",
    65: "rain",
    66: "rain",  # freezing rain
    67: "rain",
    71: "snow",
    73: "snow",
    75: "snow",
    77: "snow",  # snow grains
    80: "rain",  # rain showers
    81: "rain",
    82: "rain",
    85: "snow",  # snow showers
    86: "snow",
    95: "storm",
    96: "storm",
    99: "storm",
}


def sky_for(wmo_code: int) -> Sky | None:
    """Map a WMO present-weather code to the preset's sky vocabulary."""
    return _WMO_SKY.get(int(wmo_code))


def build_url(lat: float, lon: float) -> str:
    """Build the Open-Meteo request URL for the current conditions."""
    return (
        f"{_API_URL}?latitude={lat}&longitude={lon}"
        f"&current={_CURRENT_FIELDS}&timezone=UTC"
    )


def snapshot_from_payload(payload: dict[str, Any]) -> WeatherSnapshot | None:
    """Convert an Open-Meteo response body into a snapshot, or ``None``."""
    current = payload.get("current")
    if not isinstance(current, dict):
        return None
    try:
        code = int(current["weather_code"])
        temperature_c = float(current["temperature_2m"])
        wind_dir_deg = float(current["wind_direction_10m"])
        wind_speed_kmh = float(current["wind_speed_10m"])
    except (KeyError, TypeError, ValueError):
        return None
    sky = sky_for(code)
    if sky is None:
        return None
    observed_at = current.get("time")
    return snapshot_from_mapping(
        {
            "temperature_c": temperature_c,
            "wind_dir_deg": wind_dir_deg,
            "wind_speed_kmh": wind_speed_kmh,
            "sky": sky,
            "observed_at": str(observed_at) if observed_at is not None else None,
        }
    )


def fetch_snapshot(
    *,
    lat: float = DEFAULT_LAT,
    lon: float = DEFAULT_LON,
    timeout: float = 10.0,
) -> WeatherSnapshot | None:
    """Fetch the current conditions. Returns ``None`` on any failure."""
    raw = fetch_feed(build_url(lat, lon), timeout=timeout, max_bytes=_MAX_BYTES)
    if raw is None:
        return None
    try:
        payload = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    return snapshot_from_payload(payload)


def write_snapshot(snapshot: WeatherSnapshot, path: Path) -> Path:
    """Write the snapshot atomically: tmp in the same dir, fsync, rename.

    ``mario generate`` may read this file while the fetch runs, so a reader must
    see either the previous complete snapshot or the new complete one, never a
    truncated JSON.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "temperature_c": snapshot.temperature_c,
        "wind_dir_deg": snapshot.wind_dir_deg,
        "wind_speed_kmh": snapshot.wind_speed_kmh,
        "sky": snapshot.sky,
        "observed_at": snapshot.observed_at,
    }
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    try:
        dir_fd = os.open(str(path.parent), os.O_DIRECTORY)
    except (OSError, AttributeError):
        return path
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)
    return path
