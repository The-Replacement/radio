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
    template_version: str = Field(default="mario/2")
    tts_backend: str = Field(default="piper")
    voices_path: Path | None = Field(default=None)
    voice: str = Field(default="af_sarah")
    tts_speed: float = Field(default=1.0, gt=0)

    @classmethod
    def from_env(cls) -> MarioConfig:
        voices = os.environ.get("MARIO_VOICES_PATH")
        return cls(
            music_dir=Path(os.environ.get("MARIO_MUSIC_DIR", "/music")),
            host_dir=Path(os.environ.get("MARIO_HOST_DIR", "/data/host")),
            model=Path(os.environ.get("MARIO_MODEL", "/models/piper/es_ES-sharvard-medium.onnx")),
            time_bucket_hours=int(os.environ.get("MARIO_TIME_BUCKET_HOURS", "1")),
            model_lang=os.environ.get("MARIO_MODEL_LANG", "es"),
            template_version=os.environ.get("MARIO_TEMPLATE_VERSION", "mario/2"),
            tts_backend=os.environ.get("MARIO_TTS_BACKEND", "piper"),
            voices_path=Path(voices) if voices else None,
            voice=os.environ.get("MARIO_VOICE", "af_sarah"),
            tts_speed=float(os.environ.get("MARIO_TTS_SPEED", "1.0")),
        )
