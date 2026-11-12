"""Locate bundled preset files across source, installed, and container layouts.

Presets ship next to the source tree, not inside the installed package, so a
plain ``Path(__file__).parents[2]`` breaks as soon as Mario is pip-installed
(the container image does exactly that). Resolution is therefore an explicit
search chain, with an environment override for operators:

1. ``MARIO_PRESETS_DIR`` — explicit operator config, wins when set.
2. ``/app/presets`` — the layout the Dockerfile bakes.
3. ``<repo>/presets`` — source checkout.
4. ``<site-packages>/presets`` — in case presets are ever bundled there.
"""

from __future__ import annotations

import os
from pathlib import Path

PRESETS_DIR_ENV = "MARIO_PRESETS_DIR"

# The Dockerfile copies the repo's presets/ here; see its COPY lines.
_CONTAINER_PRESETS_DIR = Path("/app/presets")


def preset_search_paths(name: str) -> tuple[Path, ...]:
    """Return the candidate locations for preset ``name``, in priority order."""
    module_dir = Path(__file__).resolve().parent
    candidates: list[Path] = []
    override = os.environ.get(PRESETS_DIR_ENV)
    if override is not None and override.strip():
        candidates.append(Path(override.strip()) / name)
    candidates.append(_CONTAINER_PRESETS_DIR / name)
    # Source checkout: <repo>/src/mario/presets.py -> <repo>/presets/<name>.
    candidates.append(module_dir.parents[1] / "presets" / name)
    # Installed package: <site-packages>/mario -> <site-packages>/presets/<name>.
    candidates.append(module_dir.parent / "presets" / name)
    return tuple(candidates)


def resolve_preset_path(name: str) -> Path | None:
    """Return the first existing preset path for ``name``, or None."""
    for candidate in preset_search_paths(name):
        if candidate.is_file():
            return candidate
    return None


def require_preset_path(name: str) -> Path:
    """Return the preset path for ``name``. Raises when it cannot be found."""
    path = resolve_preset_path(name)
    if path is None:
        searched = ", ".join(str(candidate) for candidate in preset_search_paths(name))
        raise FileNotFoundError(f"preset {name!r} not found; searched: {searched}")
    return path
