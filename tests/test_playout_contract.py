"""Producer-consumer contract tests for playout filenames and paths (S4/#81).

These tests verify that Mario's naming helpers produce filenames that match
what Liquidsoap expects (see docs/PLAYOUT-CONTRACT.md). If a naming helper
changes, these tests go red — that is the point.
"""

from __future__ import annotations

import re
from pathlib import Path

from mario.liners import liner_cache_filename
from mario.render import cache_filename
from mario.rss import rss_stable_alias_filename
from mario.timebucket import sanitize_bucket
from mario.weather import weather_alias_filename

BUCKET = "2026-11-13T14"
BUCKET_SAN = "2026-11-13T14"  # sanitized (no special chars in this bucket)

LIQ_PATH = Path(__file__).resolve().parents[1] / "liquidsoap" / "radio.liq"


def test_sanitize_bucket_no_change_for_simple_bucket() -> None:
    assert sanitize_bucket("2026-11-13T14") == "2026-11-13T14"


def test_sanitize_bucket_colons_and_slashes() -> None:
    assert sanitize_bucket("2026-11-13T14:00") == "2026-11-13T14-00"
    assert sanitize_bucket("a/b:c") == "a_b-c"


def test_transition_filename_matches_liquidsoap_pattern() -> None:
    assert cache_filename(100, 200, BUCKET) == "100_200_2026-11-13T14.wav"


def test_weather_alias_matches_liquidsoap_path() -> None:
    assert weather_alias_filename(BUCKET) == "weather_2026-11-13T14.wav"


def test_rss_stable_alias_matches_liquidsoap_path() -> None:
    assert rss_stable_alias_filename(BUCKET) == "rss_2026-11-13T14.wav"


def test_liner_cache_filename_matches_liquidsoap_glob() -> None:
    assert liner_cache_filename("station", 100, BUCKET) == "liner_station_100_2026-11-13T14.wav"


def test_liner_glob_pattern_matches_cache_filename() -> None:
    """Liquidsoap globs liner_*_{seed}_{bucket}.wav — verify the cache filename
    matches that pattern."""
    name = liner_cache_filename("station", 100, BUCKET)
    pattern = rf"liner_.*_100_{BUCKET_SAN}\.wav"
    assert re.match(pattern, name), f"{name} does not match {pattern}"


# --- Liquidsoap consumer-side verification ---


def _liq_text() -> str:
    return LIQ_PATH.read_text(encoding="utf-8")


def test_liquidsoap_reads_current_bucket_marker() -> None:
    liq = _liq_text()
    assert 'file.exists("/host/current-bucket")' in liq
    assert 'file.contents("/host/current-bucket")' in liq


def test_liquidsoap_transition_path_matches_mario() -> None:
    """Liquidsoap constructs /host/{es}_{ss}_{bucket}.wav — must match
    Mario's cache_filename(es, ss, bucket)."""
    liq = _liq_text()
    assert 'host_path = "/host/#{es}_#{ss}_#{bucket}.wav"' in liq
    # Verify the pattern matches Mario's output
    assert cache_filename(100, 200, BUCKET) == "100_200_2026-11-13T14.wav"


def test_liquidsoap_rss_path_matches_mario() -> None:
    """Liquidsoap reads /host/rss_{bucket}.wav — must match
    Mario's rss_stable_alias_filename(bucket)."""
    liq = _liq_text()
    assert '"/host/rss_#{bucket}.wav"' in liq
    assert rss_stable_alias_filename(BUCKET) == "rss_2026-11-13T14.wav"


def test_liquidsoap_weather_path_matches_mario() -> None:
    """Liquidsoap reads /host/weather_{bucket}.wav — must match
    Mario's weather_alias_filename(bucket)."""
    liq = _liq_text()
    assert '"/host/weather_#{bucket}.wav"' in liq
    assert weather_alias_filename(BUCKET) == "weather_2026-11-13T14.wav"


def test_liquidsoap_slot_paths_use_single_expression() -> None:
    """#124: the rss/weather fallback must be one expression.

    A reassignment inside an `if` block binds a shadowed variable that dies
    with the block, so the exists-check after `end` reads the outer value and
    the bucket-scoped fallback never fires (Warning 4: Unused variable).
    """
    liq = _liq_text()
    assert 'rss_path = if file.exists("/host/rss.wav") then' in liq
    assert 'weather_path = if file.exists("/host/weather.wav") then' in liq


def test_liquidsoap_liner_glob_matches_mario() -> None:
    """Liquidsoap globs liner_*_{ss}_{bucket}.wav — must match Mario's
    liner_cache_filename pattern."""
    liq = _liq_text()
    assert 'file.ls(pattern="liner_*_#{ss}_#{bucket}.wav", "/host")' in liq
    # Verify Mario's filename matches this glob
    name = liner_cache_filename("station", 100, BUCKET)
    # The glob pattern liner_*_100_2026-11-13T14.wav should match
    glob_pat = f"liner_*_100_{BUCKET_SAN}.wav"
    import fnmatch

    assert fnmatch.fnmatch(name, glob_pat), f"{name} does not match glob {glob_pat}"


def test_liquidsoap_jingle_pool_is_audio_only() -> None:
    """#121: single() on README.md or .gitkeep raises "infallible source not
    able to prepare in time", aborts the clock and kills the container. The
    jingle pool must be filtered to audio extensions before list.nth picks."""
    liq = _liq_text()
    assert 'pattern="\\\\.(mp3|wav|flac|ogg)$"' in liq

    import re

    pool_filter = re.compile(r"\.(mp3|wav|flac|ogg)$")
    assert pool_filter.search("station-jingle.wav")
    assert not pool_filter.search("README.md")
    assert not pool_filter.search(".gitkeep")


def test_liquidsoap_spacers_are_fallible_safe_single() -> None:
    """#123: single() is statically infallible when its file exists at
    wake-up; the hourly TTS prune can unlink the path before the EOF
    re-fetch, and the infallible source.dynamic wrapper then aborts the
    clock (reproduced on the pinned image: "Infallible source.dynamic
    single was not able to prepare a source in time"). Every spacer must go
    through safe_single: play-once, synchronous single fetch, explicit
    resolve, visible skip log. A bare single( call is a regression to the
    abort."""
    liq = _liq_text()
    assert "def safe_single(uri)" in liq
    # Exactly the five spacer call sites (host, rss, weather, ident, jingle).
    assert liq.count("[safe_single(") == 5
    # The helper must keep the semantics proven in the design probes:
    # synchronous (else sequence skips the spacer before it ever fetches)
    # and a visible warning naming the skipped path (else degradation is
    # silent).
    assert "request.dynamic(synchronous=true, next)" in liq
    assert 'log.important(label="safe_single"' in liq
    # No bare single( in code: underscore is a word character, so the
    # lookbehind does not match safe_single(; comments are stripped because
    # they legitimately discuss single() by name.
    code = "\n".join(line for line in liq.splitlines() if not line.lstrip().startswith("#"))
    assert re.search(r"(?<![A-Za-z0-9_])single\(", code) is None


# --- Caddy consumer-side verification ---


CADDY_PATH = Path(__file__).resolve().parents[1] / "caddy" / "Caddyfile"


def test_caddy_host_mapping() -> None:
    """Caddy serves /host/* from /srv-host (Mario's host_dir mount)."""
    caddy = CADDY_PATH.read_text(encoding="utf-8")
    assert "handle_path /host/*" in caddy
    assert "root * /srv-host" in caddy


def test_caddy_music_mapping() -> None:
    """Caddy serves /music/* from /srv-music (sidecars)."""
    caddy = CADDY_PATH.read_text(encoding="utf-8")
    assert "handle_path /music/*" in caddy
    assert "root * /srv-music" in caddy


def test_caddy_cam_mapping() -> None:
    """Caddy serves /cam/* from /srv-cam (HLS segments)."""
    caddy = CADDY_PATH.read_text(encoding="utf-8")
    assert "handle_path /cam/*" in caddy
    assert "root * /srv-cam" in caddy
