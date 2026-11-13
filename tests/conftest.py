from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest


def install_fake_kokoro(monkeypatch: pytest.MonkeyPatch, *, marker: bytes = b"RIFF") -> dict[str, Any]:
    calls: dict[str, Any] = {"n": 0, "creates": 0}

    class FakeKokoro:
        def __init__(self, model: str, voices: str) -> None:
            calls["model"] = model
            calls["voices"] = voices

        def create(self, text: str, voice: str, speed: float, lang: str) -> tuple[list[float], int]:
            calls["n"] += 1
            calls["creates"] += 1
            calls["text"] = text
            calls["voice"] = voice
            calls["speed"] = speed
            calls["lang"] = lang
            return [0.0, 0.0], 24000

    def fake_write(path: str, samples: object, sample_rate: int, format: str = "WAV") -> None:
        Path(path).write_bytes(marker)

    monkeypatch.setitem(sys.modules, "kokoro_onnx", SimpleNamespace(Kokoro=FakeKokoro))
    monkeypatch.setitem(sys.modules, "soundfile", SimpleNamespace(write=fake_write))
    return calls


@pytest.fixture
def kokoro_env(tmp_path: Path) -> tuple[Path, Path]:
    model = tmp_path / "kokoro.onnx"
    voices = tmp_path / "voices.bin"
    model.write_bytes(b"m")
    voices.write_bytes(b"v")
    return model, voices
