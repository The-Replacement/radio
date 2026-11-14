"""Issue #109: the cache must follow what would be spoken, not just the filename.

Before this, the skip rule was "the file exists", so editing the phrase
constants in text.py — or bumping MARIO_TEMPLATE_VERSION, which the module
docstring promised would invalidate the cache — shipped stale audio silently.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mario.render import (
    _identity_path,
    generate_liner_segment,
    generate_segment,
    generate_weather_segment,
    render_identity,
)
from tests.conftest import install_fake_kokoro

BUCKET = "2026-11-14T12"


def _render(model: Path, voices: Path, host: Path, *, text: str, template_version: str = "mario/2") -> Path:
    return generate_segment(
        100,
        200,
        BUCKET,
        text,
        model,
        host,
        voices_path=voices,
        template_version=template_version,
    )


def test_same_identity_is_a_cache_hit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kokoro_env: tuple[Path, Path]
) -> None:
    calls = install_fake_kokoro(monkeypatch)
    model, voices = kokoro_env
    _render(model, voices, tmp_path, text="Hola")
    _render(model, voices, tmp_path, text="Hola")
    assert calls["n"] == 1


def test_changed_text_rerenders_the_same_filename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kokoro_env: tuple[Path, Path]
) -> None:
    """Editing text.py must not leave the old audio in place (the #109 trap)."""
    calls = install_fake_kokoro(monkeypatch)
    model, voices = kokoro_env
    first = _render(model, voices, tmp_path, text="Hola")
    second = _render(model, voices, tmp_path, text="Buenas noches")
    assert first == second, "the playout filename is unchanged, by contract"
    assert calls["n"] == 2


def test_template_version_bump_invalidates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kokoro_env: tuple[Path, Path]
) -> None:
    """The documented lever: same text, bumped version, fresh audio."""
    calls = install_fake_kokoro(monkeypatch)
    model, voices = kokoro_env
    _render(model, voices, tmp_path, text="Hola", template_version="mario/2")
    _render(model, voices, tmp_path, text="Hola", template_version="mario/3")
    assert calls["n"] == 2


def test_voice_and_speed_are_part_of_the_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kokoro_env: tuple[Path, Path]
) -> None:
    calls = install_fake_kokoro(monkeypatch)
    model, voices = kokoro_env
    generate_segment(1, 2, BUCKET, "Hola", model, tmp_path, voices_path=voices, voice="ef_dora")
    generate_segment(1, 2, BUCKET, "Hola", model, tmp_path, voices_path=voices, voice="em_alex")
    assert calls["n"] == 2


def test_legacy_file_without_sidecar_rerenders_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kokoro_env: tuple[Path, Path]
) -> None:
    """A pool rendered before identities existed must heal, then cache again."""
    calls = install_fake_kokoro(monkeypatch)
    model, voices = kokoro_env
    target = _render(model, voices, tmp_path, text="Hola")
    _identity_path(target).unlink()

    _render(model, voices, tmp_path, text="Hola")
    assert calls["n"] == 2, "unknown provenance means re-render, once"
    _render(model, voices, tmp_path, text="Hola")
    assert calls["n"] == 2, "and then it is a cache hit again"


def test_sidecar_is_valid_json_with_the_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kokoro_env: tuple[Path, Path]
) -> None:
    install_fake_kokoro(monkeypatch)
    model, voices = kokoro_env
    target = _render(model, voices, tmp_path, text="Hola")
    stamped = json.loads(_identity_path(target).read_text(encoding="utf-8"))
    expected = render_identity(
        text="Hola", template_version="mario/2", voice="ef_dora", speed=1.0, lang="es"
    )
    assert stamped == {"identity": expected}


def test_liners_and_weather_use_the_same_rule(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kokoro_env: tuple[Path, Path]
) -> None:
    calls = install_fake_kokoro(monkeypatch)
    model, voices = kokoro_env
    generate_liner_segment("station", 5, BUCKET, "Somos radio", model, tmp_path, voices_path=voices)
    generate_liner_segment("station", 5, BUCKET, "Otra cosa", model, tmp_path, voices_path=voices)
    generate_weather_segment("abc12345", BUCKET, "18 grados", model, tmp_path, voices_path=voices)
    generate_weather_segment("abc12345", BUCKET, "21 grados", model, tmp_path, voices_path=voices)
    assert calls["n"] == 4


def test_sink_forwards_the_config_template_version_for_transitions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A plumbing guard: the sink must not drop template_version.

    The unit tests above call the generators directly, so they would keep
    passing if gen.py stopped threading the version through — and then a
    version bump would silently stop invalidating transitions.
    """
    from mario import gen
    from mario.config import MarioConfig

    captured: dict[str, object] = {}

    def fake_generate_segment(*args: object, **kwargs: object) -> Path:
        captured.update(kwargs)
        return Path("/dev/null")

    monkeypatch.setattr(gen, "generate_segment", fake_generate_segment)
    ctx = gen._GenerationContext(
        config=MarioConfig(
            host_dir=tmp_path,
            template_version="mario/testing",
            voices_path=None,
            model=tmp_path / "m.onnx",
        ),
        tracks=[],
        bucket=BUCKET,
        kokoro_lang="es",
        dry_run=False,
        force=False,
        feed_fetcher=None,
        keep_buckets=None,
    )
    sink = gen._SegmentSink(ctx, [])
    sink.transition(1, 2, "Hola", 1.0)

    assert captured["template_version"] == "mario/testing"
