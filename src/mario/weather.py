"""Weather snapshot loading, hashing, and deterministic text for The Meteorologist."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml  # type: ignore[import-untyped]

from .timebucket import sanitize_bucket

Sky = Literal["clear", "partly_cloudy", "overcast", "rain", "storm", "snow", "fog"]

_VALID_SKY: tuple[Sky, ...] = ("clear", "partly_cloudy", "overcast", "rain", "storm", "snow", "fog")

FALLBACK_WEATHER_HASH: str = hashlib.sha256(b"fallback").hexdigest()

_COMPASS_16: tuple[str, ...] = (
    "norte",
    "nornoreste",
    "noreste",
    "estenordeste",
    "este",
    "estsudeste",
    "sudeste",
    "sudsudeste",
    "sur",
    "sudsuroeste",
    "sudoeste",
    "oestsudoeste",
    "oeste",
    "oestenoroeste",
    "noroeste",
    "nornoroeste",
)

_SKY_PHRASES: dict[Sky, str] = {
    "clear": "despejado",
    "partly_cloudy": "parcialmente nublado",
    "overcast": "cubierto",
    "rain": "lluvia",
    "storm": "tormenta",
    "snow": "nieve",
    "fog": "neblina",
}


@dataclass(frozen=True)
class WeatherSnapshot:
    temperature_c: float
    wind_dir_deg: float
    wind_speed_kmh: float
    sky: Sky
    observed_at: str | None = None


@dataclass(frozen=True)
class WeatherPreset:
    template_version: str
    template: str
    fallback: str


def compass_16(deg: float) -> str:
    """Return a deterministic 16-point compass direction (blowing from)."""
    d = float(deg) % 360.0
    idx = int((d + 11.25) // 22.5) % 16
    return _COMPASS_16[idx]


def load_weather_preset(path: Path) -> WeatherPreset:
    """Load weather preset YAML."""
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise TypeError("weather preset must be a mapping")
    version = str(data.get("template_version", "mario/weather/1"))
    template = str(data.get("template", "")).strip()
    fallback = str(data.get("fallback", "")).strip()
    if not template or not fallback:
        raise ValueError("weather preset requires non-empty 'template' and 'fallback'")
    return WeatherPreset(template_version=version, template=template, fallback=fallback)


def _coerce_snapshot(data: dict[str, Any]) -> WeatherSnapshot | None:
    try:
        temperature_c = float(data["temperature_c"])
        wind_dir_deg = float(data["wind_dir_deg"])
        wind_speed_kmh = float(data["wind_speed_kmh"])
        sky_raw = str(data["sky"]).strip().lower()
    except (KeyError, TypeError, ValueError):
        return None
    if sky_raw not in _VALID_SKY:
        return None
    sky = sky_raw
    observed_at_val = data.get("observed_at")
    observed_at = str(observed_at_val) if observed_at_val is not None else None
    # Normalize direction into [0, 360).
    wind_dir_deg = wind_dir_deg % 360.0
    return WeatherSnapshot(
        temperature_c=temperature_c,
        wind_dir_deg=wind_dir_deg,
        wind_speed_kmh=wind_speed_kmh,
        sky=sky,
        observed_at=observed_at,
    )


def load_weather_snapshot(path: Path) -> WeatherSnapshot | None:
    """Load and validate a local weather snapshot JSON. Returns None on any failure."""
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(data, dict):
        return None
    return _coerce_snapshot(data)


def canonical_weather_payload(snapshot: WeatherSnapshot) -> dict[str, Any]:
    """Canonical spoken-field payload for hashing (excludes observed_at)."""
    return {
        "temperature_c": float(snapshot.temperature_c),
        "wind_dir_deg": float(snapshot.wind_dir_deg % 360.0),
        "wind_speed_kmh": float(snapshot.wind_speed_kmh),
        "sky": str(snapshot.sky),
    }


def weather_snapshot_hash(snapshot: WeatherSnapshot) -> str:
    """SHA-256 over canonical serialization of parsed, normalized spoken fields."""
    payload = canonical_weather_payload(snapshot)
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def format_temperature_c(value: float) -> str:
    """Deterministic temperature phrase."""
    rounded = round(float(value))
    if abs(float(value) - rounded) < 1e-9:
        n = int(rounded)
    else:
        # keep one decimal deterministically
        n_str = f"{float(value):.1f}".rstrip("0").rstrip(".")
        return f"{n_str} grados Celsius"
    return f"{n} grados Celsius"


def format_wind_speed_kmh(value: float) -> str:
    rounded = round(float(value))
    if abs(float(value) - rounded) < 1e-9:
        n = int(rounded)
        return f"{n} kilómetros por hora"
    n_str = f"{float(value):.1f}".rstrip("0").rstrip(".")
    return f"{n_str} kilómetros por hora"


def build_weather_text(snapshot: WeatherSnapshot | None, preset: WeatherPreset) -> tuple[str, str]:
    """Return (text, hash). Fallback is deterministic and never raises."""
    if snapshot is None:
        return preset.fallback, FALLBACK_WEATHER_HASH
    text = preset.template.format(
        temperature=format_temperature_c(snapshot.temperature_c),
        wind_dir=compass_16(snapshot.wind_dir_deg),
        wind_speed=format_wind_speed_kmh(snapshot.wind_speed_kmh),
        sky=_SKY_PHRASES[snapshot.sky],
    )
    return text, weather_snapshot_hash(snapshot)


def weather_cache_filename(hash_hex: str, bucket: str) -> str:
    return f"weather_{hash_hex[:8]}_{sanitize_bucket(bucket)}.wav"


def weather_alias_filename(bucket: str) -> str:
    return f"weather_{sanitize_bucket(bucket)}.wav"
