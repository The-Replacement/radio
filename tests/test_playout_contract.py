"""Producer-consumer contract tests for playout filenames and paths (S4/#81).

These tests verify that Mario's naming helpers produce filenames that match
what Liquidsoap expects (see docs/PLAYOUT-CONTRACT.md). If a naming helper
changes, these tests go red — that is the point.
"""

from __future__ import annotations

from mario.liners import liner_cache_filename
from mario.render import cache_filename
from mario.rss import rss_stable_alias_filename
from mario.timebucket import sanitize_bucket
from mario.weather import weather_alias_filename


BUCKET = "2026-11-13T14"


def test_sanitize_bucket_no_change_for_simple_bucket():
    assert sanitize_bucket("2026-11-13T14") == "2026-11-13T14"


def test_sanitize_bucket_colons_and_slashes():
    assert sanitize_bucket("2026-11-13T14:00") == "2026-11-13T14-00"
    assert sanitize_bucket("a/b:c") == "a_b-c"


def test_transition_filename_matches_liquidsoap_pattern():
    assert cache_filename(100, 200, BUCKET) == "100_200_2026-11-13T14.wav"


def test_weather_alias_matches_liquidsoap_path():
    assert weather_alias_filename(BUCKET) == "weather_2026-11-13T14.wav"


def test_rss_stable_alias_matches_liquidsoap_path():
    assert rss_stable_alias_filename(BUCKET) == "rss_2026-11-13T14.wav"


def test_liner_cache_filename_matches_liquidsoap_glob():
    assert liner_cache_filename("station", 100, BUCKET) == "liner_station_100_2026-11-13T14.wav"


def test_liner_glob_pattern_matches_cache_filename():
    """Liquidsoap globs liner_*_{seed}_{bucket}.wav — verify the cache filename
    matches that pattern."""
    import re

    name = liner_cache_filename("station", 100, BUCKET)
    pattern = rf"liner_.*_100_{BUCKET.replace(':', '-')}\.wav"
    assert re.match(pattern, name), f"{name} does not match {pattern}"