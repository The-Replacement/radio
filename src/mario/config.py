"""Configuration seams for Mario.

Every value that changes between deployments comes from the environment (or
CLI flags), never from a hardcoded constant. Defaults are safe for local dev.
"""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel, Field


class MarioConfig(BaseModel):
    music_dir: Path = Field(default=Path("/music"))
    host_dir: Path = Field(default=Path("/data/host"))
    model: Path = Field(default=Path("/models/piper/es_ES-sharvard-medium.onnx"))
    time_bucket_hours: int = Field(default=1, ge=1)
    model_lang: str = Field(default="es")
    template_version: str = Field(default="mario/1")

    @classmethod
    def from_env(cls) -> MarioConfig:
        return cls(
            music_dir=Path(os.environ.get("MARIO_MUSIC_DIR", "/music")),
            host_dir=Path(os.environ.get("MARIO_HOST_DIR", "/data/host")),
            model=Path(os.environ.get("MARIO_MODEL", "/models/piper/es_ES-sharvard-medium.onnx")),
            time_bucket_hours=int(os.environ.get("MARIO_TIME_BUCKET_HOURS", "1")),
            model_lang=os.environ.get("MARIO_MODEL_LANG", "es"),
            template_version=os.environ.get("MARIO_TEMPLATE_VERSION", "mario/1"),
        )
