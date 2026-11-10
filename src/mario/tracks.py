"""Read a radio music library into TrackMeta records.

Catalog-first (indiegen/catalog/1), with a sidecar scan fallback for libraries
that have no catalog.json. Mario never writes to the music directory.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class TrackMeta:
    seed: int
    lane: str
    form: str
    preset: str
    pcm_sha256: str
    path: str
    duration_seconds: float


def _from_sidecar(payload: dict[str, Any], path: Path) -> TrackMeta | None:
    plan = payload.get("plan")
    measurements = payload.get("measurements")
    if not isinstance(plan, dict) or not isinstance(measurements, dict):
        return None
    digest = payload.get("pcm_sha256")
    if not isinstance(digest, str):
        return None
    return TrackMeta(
        seed=int(payload.get("seed", 0)),
        lane=str(payload.get("lane", "")),
        form=str(plan.get("form", "")),
        preset=str(payload.get("preset", "")),
        pcm_sha256=digest,
        path=str(path),
        duration_seconds=float(measurements.get("duration_seconds", 0.0)),
    )


def _read_catalog(music_dir: Path) -> list[TrackMeta] | None:
    catalog_path = music_dir / "catalog.json"
    if not catalog_path.is_file():
        return None
    try:
        data = json.loads(catalog_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("schema") != "indiegen/catalog/1":
        return None
    tracks = data.get("tracks")
    if not isinstance(tracks, dict):
        return None
    out: list[TrackMeta] = []
    for digest, entry in tracks.items():
        if not isinstance(entry, dict):
            continue
        # Catalog entries lack the sidecar's plan; fall back to sidecar if present.
        stem = Path(str(entry.get("path", ""))).stem
        sidecar_path = music_dir / (stem + ".json")
        if sidecar_path.is_file():
            try:
                sp = json.loads(sidecar_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                sp = None
            if isinstance(sp, dict) and sp.get("schema") == "indiegen/sidecar/1":
                tm = _from_sidecar(sp, Path(entry.get("path", "")))
                if tm is not None:
                    out.append(tm)
                    continue
        out.append(
            TrackMeta(
                seed=int(entry.get("seed", 0)),
                lane=str(entry.get("lane", "")),
                form=str(entry.get("form", "")),
                preset=str(entry.get("preset", "")),
                pcm_sha256=str(digest),
                path=str(entry.get("path", "")),
                duration_seconds=float(entry.get("duration_seconds", 0.0)),
            )
        )
    return out


def load_tracks(music_dir: Path) -> list[TrackMeta]:
    """Load tracks from a music library directory."""
    music_dir = Path(music_dir)
    from_catalog = _read_catalog(music_dir)
    if from_catalog is not None:
        return from_catalog
    # Fallback: scan sidecars.
    out: list[TrackMeta] = []
    for sidecar in sorted(music_dir.glob("*.json")):
        if sidecar.name == "catalog.json":
            continue
        try:
            payload = json.loads(sidecar.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(payload, dict) or payload.get("schema") != "indiegen/sidecar/1":
            continue
        tm = _from_sidecar(payload, sidecar.with_suffix(".flac"))
        if tm is not None:
            out.append(tm)
    return out
