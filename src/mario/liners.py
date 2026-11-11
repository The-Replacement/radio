"""Station idents (liners): deterministic pool, selection, and rendering."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import yaml  # type: ignore[import-untyped]

from .timebucket import sanitize_bucket


@dataclass(frozen=True)
class Liner:
    ident_id: str
    text: str


@dataclass(frozen=True)
class LinerPool:
    template_version: str
    liners: tuple[Liner, ...]


def load_liners(path: Path) -> LinerPool:
    """Load liner presets from a YAML file."""
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise TypeError("liners preset must be a mapping")
    version = str(data.get("template_version", "mario/liners/1"))
    raw = data.get("idents")
    if not isinstance(raw, list) or not raw:
        raise ValueError("liners preset must contain a non-empty 'idents' list")
    liners: list[Liner] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            raise TypeError("each ident must be a mapping")
        ident_id = str(item.get("id", "")).strip()
        text = str(item.get("text", "")).strip()
        if not ident_id or not text:
            raise ValueError("each ident requires non-empty 'id' and 'text'")
        if ident_id in seen:
            raise ValueError(f"duplicate ident id: {ident_id}")
        seen.add(ident_id)
        liners.append(Liner(ident_id=ident_id, text=text))
    return LinerPool(template_version=version, liners=tuple(liners))


def select_liner(pool: LinerPool, seed: int) -> Liner:
    """Deterministically select a liner from the pool by seed."""
    if not pool.liners:
        raise ValueError("liner pool is empty")
    return pool.liners[seed % len(pool.liners)]


def liner_cache_filename(ident_id: str, seed: int, bucket: str) -> str:
    """Cache filename keyed by (ident_id, seed, time_bucket)."""
    safe_id = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in ident_id)
    return f"liner_{safe_id}_{seed}_{sanitize_bucket(bucket)}.wav"


def liner_cache_key(ident_id: str, seed: int, bucket: str, template_version: str) -> str:
    """Stable identity for a liner render request (for callers/tests)."""
    raw = f"{template_version}\0{ident_id}\0{seed}\0{bucket}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
