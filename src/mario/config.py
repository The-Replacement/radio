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
    liners_preset: Path | None = Field(default=None)
    weather_file: Path | None = Field(default=None)
    rss_feeds_file: Path | None = Field(default=None)
    rss_cache_dir: Path | None = Field(default=None)
    rss_max_items_per_cycle: int = Field(default=1, ge=1)
    rss_max_text_chars: int = Field(default=600, ge=1)
    rss_timeout_seconds: float = Field(default=10.0, gt=0)

    @classmethod
    def from_env(cls) -> MarioConfig:
        voices = os.environ.get("MARIO_VOICES_PATH")
        liners = os.environ.get("MARIO_LINERS_PRESET")
        weather = os.environ.get("MARIO_WEATHER_FILE")
        rss_feeds = os.environ.get("MARIO_RSS_FEEDS_FILE")
        rss_cache = os.environ.get("MARIO_RSS_CACHE_DIR")
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
            liners_preset=Path(liners) if liners else None,
            weather_file=Path(weather) if weather else None,
            rss_feeds_file=Path(rss_feeds) if rss_feeds else None,
            rss_cache_dir=Path(rss_cache) if rss_cache else None,
            rss_max_items_per_cycle=int(
                os.environ.get("MARIO_RSS_MAX_ITEMS_PER_CYCLE", "1")
            ),
            rss_max_text_chars=int(os.environ.get("MARIO_RSS_MAX_TEXT_CHARS", "600")),
            rss_timeout_seconds=float(os.environ.get("MARIO_RSS_TIMEOUT_SECONDS", "10.0")),
        )
