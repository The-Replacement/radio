"""Tests for the RSS reader (The Reader)."""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Self

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from mario.config import MarioConfig
from mario.feedfetch import fetch_feed
from mario.gen import generate
from mario.rss import (
    FeedConfig,
    FeedItem,
    RssParseError,
    RssPreset,
    build_rss_fallback_text,
    build_rss_text,
    feed_cache_filename,
    feed_slug,
    load_feed_bytes,
    load_rss_feeds,
    load_rss_preset,
    normalize_feed_url,
    parse_feed_items,
    rss_alias_filename,
    rss_cache_filename,
    rss_cache_key,
    rss_fallback_filename,
    rss_stable_alias_filename,
    select_feed_item,
    strip_html,
    truncate_text,
)

BUCKET = "2026-11-10T14"
MODEL_BYTES = b"model"


# --------------------------------------------------------------------------- helpers


class FakeFetcher:
    """Callable feed fetcher that records calls and never touches a network."""

    def __init__(self, responses: dict[str, bytes | None]) -> None:
        self.responses = responses
        self.calls: list[str] = []

    def __call__(self, url: str) -> bytes | None:
        self.calls.append(url)
        return self.responses.get(url)


def _blow_up(url: str) -> bytes | None:
    raise AssertionError(f"fetcher must not be called: {url}")


def _install_tts(monkeypatch: pytest.MonkeyPatch, payload: bytes = b"RIFF-RSS") -> dict[str, int]:
    calls = {"n": 0}

    def fake_run(cmd: list[str], **kwargs: Any) -> object:
        calls["n"] += 1
        out = Path(cmd[cmd.index("-f") + 1])
        out.write_bytes(payload)

        class Result:
            returncode = 0
            stdout = b""
            stderr = b""

        return Result()

    monkeypatch.setattr(subprocess, "run", fake_run)
    return calls


def _model(tmp_path: Path) -> Path:
    model = tmp_path / "model.onnx"
    model.write_bytes(MODEL_BYTES)
    return model


def _config(tmp_path: Path, name: str, **overrides: Any) -> MarioConfig:
    base = tmp_path / name
    kwargs: dict[str, Any] = {
        "music_dir": base / "music",
        "host_dir": base / "host",
        "model": _model(tmp_path),
        "rss_cache_dir": base / "cache",
    }
    kwargs.update(overrides)
    return MarioConfig(**kwargs)


def _feeds_file(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "feeds.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def _sidecar(music_dir: Path, seed: int) -> None:
    import json

    payload = {
        "schema": "indiegen/sidecar/1",
        "seed": seed,
        "lane": "motorik",
        "preset": "motorik",
        "pcm_sha256": f"hash{seed}",
        "plan": {"form": "A", "techniques": [], "sections": [], "director_log": []},
        "measurements": {
            "duration_seconds": 10.0,
            "integrated_lufs": -16.0,
            "true_peak_dbtp": -1.5,
            "loudness_range_lu": 5.0,
        },
    }
    music_dir.mkdir(parents=True, exist_ok=True)
    (music_dir / f"track{seed}.json").write_text(json.dumps(payload), encoding="utf-8")


# --------------------------------------------------------------------------- fixtures

_ITEMS: tuple[str, ...] = (
    (
        "<item><guid>g1</guid><title>First story</title>"
        "<description><![CDATA[<p>Hello <b>world</b> &amp; friends</p>]]></description>"
        "<pubDate>Tue, 10 Nov 2026 14:00:00 +0000</pubDate></item>"
    ),
    (
        "<item><title>Second story</title><link>https://example.org/b</link>"
        "<description>Second body</description></item>"
    ),
    (
        "<item><title>Third story</title><description>Third body</description>"
        "<pubDate>2026-11-09T10:00:00Z</pubDate></item>"
    ),
)


def _rss_doc(items: tuple[str, ...] | list[str]) -> bytes:
    return (
        '<rss version="2.0"><channel><title>Feed</title>'
        + "".join(items)
        + "</channel></rss>"
    ).encode("utf-8")


RSS20 = _rss_doc(_ITEMS)
RSS20_REVERSED = _rss_doc(tuple(reversed(_ITEMS)))
RSS20_DUP_GUID = _rss_doc(
    (
        "<item><guid>dup</guid><title>Alpha</title><description>a</description></item>",
        "<item><guid>dup</guid><title>Beta</title><description>b</description></item>",
    )
)
ATOM = (
    b'<feed xmlns="http://www.w3.org/2005/Atom"><title>Atom feed</title>'
    b"<entry><id>urn:uuid:1</id><title>Atom one</title>"
    b'<link href="https://example.org/atom-1"/>'
    b"<updated>2026-11-11T09:30:00Z</updated><summary>Atom summary one</summary></entry>"
    b'<entry><id>urn:uuid:2</id><title>Atom two</title>'
    b'<content type="html">&lt;p&gt;Atom body two&lt;/p&gt;</content>'
    b"<updated>2026-11-10T08:00:00Z</updated></entry></feed>"
)
RDF = (
    b'<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"'
    b' xmlns="http://purl.org/rss/1.0/"'
    b' xmlns:dc="http://purl.org/dc/elements/1.1/">'
    b'<channel rdf:about="https://example.org/rdf"><title>RDF</title></channel>'
    b'<item rdf:about="https://example.org/rdf/1"><title>RDF one</title>'
    b"<link>https://example.org/rdf/1</link><description>RDF body</description>"
    b"<dc:date>2026-11-08T07:00:00Z</dc:date></item></rdf:RDF>"
)
BROKEN_XML = b"<rss><channel><item>"
DOCTYPE_FEED = b'<!DOCTYPE rss [<!ENTITY lol "lol">]><rss version="2.0"><channel/></rss>'
ENTITY_FEED = b'<!ENTITY lol "lol"><rss version="2.0"><channel/></rss>'
NOT_A_FEED = b"<html><body>503</body></html>"
EMPTY_FEED = b'<rss version="2.0"><channel><title>Empty</title></channel></rss>'
BLANK_ITEMS = _rss_doc(("<item><title></title><description></description></item>",))


# --------------------------------------------------------------------------- config


def test_config_rss_env_and_cli(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for key in (
        "MARIO_RSS_FEEDS_FILE",
        "MARIO_RSS_CACHE_DIR",
        "MARIO_RSS_MAX_ITEMS_PER_CYCLE",
        "MARIO_RSS_MAX_TEXT_CHARS",
        "MARIO_RSS_TIMEOUT_SECONDS",
    ):
        monkeypatch.delenv(key, raising=False)

    defaults = MarioConfig.from_env()
    assert defaults.rss_feeds_file is None
    assert defaults.rss_cache_dir is None
    assert defaults.rss_max_items_per_cycle == 1
    assert defaults.rss_max_text_chars == 600
    assert defaults.rss_timeout_seconds == 10.0

    monkeypatch.setenv("MARIO_RSS_FEEDS_FILE", "/tmp/feeds.yaml")
    monkeypatch.setenv("MARIO_RSS_CACHE_DIR", "/tmp/rss-cache")
    monkeypatch.setenv("MARIO_RSS_MAX_ITEMS_PER_CYCLE", "3")
    monkeypatch.setenv("MARIO_RSS_MAX_TEXT_CHARS", "120")
    monkeypatch.setenv("MARIO_RSS_TIMEOUT_SECONDS", "2.5")

    configured = MarioConfig.from_env()
    assert configured.rss_feeds_file == Path("/tmp/feeds.yaml")
    assert configured.rss_cache_dir == Path("/tmp/rss-cache")
    assert configured.rss_max_items_per_cycle == 3
    assert configured.rss_max_text_chars == 120
    assert configured.rss_timeout_seconds == 2.5

    from mario.cli import app

    captured: dict[str, Any] = {}

    def fake_run(config: MarioConfig, **kwargs: Any) -> list[Path]:
        captured["config"] = config
        return []

    monkeypatch.setattr("mario.cli.run_generate", fake_run)
    result = CliRunner().invoke(
        app,
        [
            "generate",
            "--rss-feeds-file",
            "/tmp/cli-feeds.yaml",
            "--rss-cache-dir",
            "/tmp/cli-cache",
            "--rss-max-items-per-cycle",
            "2",
            "--rss-max-text-chars",
            "80",
            "--rss-timeout-seconds",
            "1.5",
        ],
    )
    assert result.exit_code == 0, result.output
    cfg = captured["config"]
    assert cfg.rss_feeds_file == Path("/tmp/cli-feeds.yaml")
    assert cfg.rss_cache_dir == Path("/tmp/cli-cache")
    assert cfg.rss_max_items_per_cycle == 2
    assert cfg.rss_max_text_chars == 80
    assert cfg.rss_timeout_seconds == 1.5


def test_config_rss_bounds_rejected() -> None:
    with pytest.raises(ValidationError):
        MarioConfig(rss_max_items_per_cycle=0)
    with pytest.raises(ValidationError):
        MarioConfig(rss_max_text_chars=0)
    with pytest.raises(ValidationError):
        MarioConfig(rss_timeout_seconds=0)


def test_rss_unconfigured_no_output(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls = _install_tts(monkeypatch)
    music = tmp_path / "music"
    _sidecar(music, 1)
    _sidecar(music, 2)
    cfg = _config(tmp_path, "off", music_dir=music, rss_cache_dir=None)

    paths = generate(cfg, time_bucket=BUCKET, feed_fetcher=_blow_up)

    assert calls["n"] > 0
    assert not [p.name for p in paths if p.name.startswith("rss_")]
    assert not [p.name for p in cfg.host_dir.glob("rss_*")]
    assert not (cfg.host_dir.parent / "rss-cache").exists()


# --------------------------------------------------------------------------- config file


def test_load_rss_feeds_variants(tmp_path: Path) -> None:
    bare = _feeds_file(
        tmp_path,
        "- https://example.org/one.xml\n- https://example.org/two.xml\n",
    )
    feeds = load_rss_feeds(bare)
    assert [f.normalized_url for f in feeds] == [
        "https://example.org/one.xml",
        "https://example.org/two.xml",
    ]
    assert all(f.name is None for f in feeds)
    assert [f.slug for f in feeds] == ["example-org-one-xml", "example-org-two-xml"]

    named_path = tmp_path / "named.yaml"
    named_path.write_text(
        "- name: hn\n  url: https://news.example.org/rss\n"
        "- https://example.org/plain\n",
        encoding="utf-8",
    )
    named = load_rss_feeds(named_path)
    assert named[0].name == "hn"
    assert named[0].slug == "hn"
    assert named[1].slug == "example-org-plain"

    mapping = tmp_path / "mapping.yaml"
    mapping.write_text(
        "feeds:\n  - name: atomic\n    url: https://example.org/atom.xml?x=1\n",
        encoding="utf-8",
    )
    mapped = load_rss_feeds(mapping)
    assert len(mapped) == 1
    assert mapped[0].normalized_url == "https://example.org/atom.xml?x=1"


def test_load_rss_feeds_rejects_invalid(tmp_path: Path) -> None:
    duplicate = _feeds_file(
        tmp_path,
        "- https://example.org/feed.xml\n- https://example.org/feed.xml#top\n",
    )
    with pytest.raises(ValueError):
        load_rss_feeds(duplicate)

    scheme = tmp_path / "scheme.yaml"
    scheme.write_text("- ftp://example.org/feed.xml\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_rss_feeds(scheme)

    empty = tmp_path / "empty.yaml"
    empty.write_text("feeds: []\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_rss_feeds(empty)

    missing_url = tmp_path / "missing.yaml"
    missing_url.write_text("- name: broken\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_rss_feeds(missing_url)


def test_normalize_feed_url() -> None:
    assert normalize_feed_url("HTTP://Example.ORG/a?b=1#x") == "http://example.org/a?b=1"
    assert normalize_feed_url("http://example.org/P") == normalize_feed_url(
        "http://example.org/P"
    )
    assert normalize_feed_url(normalize_feed_url("https://Example.org/f#frag")) == (
        "https://example.org/f"
    )
    with pytest.raises(ValueError):
        normalize_feed_url("file:///etc/passwd")

    a = normalize_feed_url("HTTP://EXAMPLE.org/feed.xml#one")
    b = normalize_feed_url("http://example.org/feed.xml")
    assert a == b
    assert feed_cache_filename(a, BUCKET) == feed_cache_filename(b, BUCKET)
    assert rss_cache_key(a, "k", BUCKET, "h") == rss_cache_key(b, "k", BUCKET, "h")


def test_feed_slug() -> None:
    assert feed_slug("HN Top", "https://example.org/x") == "hn-top"
    assert feed_slug(None, "https://example.org/feed.xml") == "example-org-feed-xml"
    assert feed_slug("   ", "https://example.org/feed.xml") == "example-org-feed-xml"
    assert feed_slug("!!!", "https://example.org/") == "feed"


# --------------------------------------------------------------------------- parsing


def test_parse_rss20() -> None:
    items = parse_feed_items(RSS20, max_text_chars=600)
    assert [item.item_key for item in items] == [
        "g1",
        "https://example.org/b",
        hashlib.sha256(b"Third story\x002026-11-09T10:00:00Z").hexdigest()[:16],
    ]
    assert items[0].title == "First story"
    assert items[0].body == "Hello world & friends"
    assert items[0].published_at == "2026-11-10T14:00:00Z"
    assert items[1].published_at == ""
    assert items[2].published_at == "2026-11-09T10:00:00Z"


def test_parse_atom() -> None:
    items = parse_feed_items(ATOM, max_text_chars=600)
    assert [item.item_key for item in items] == ["urn:uuid:1", "urn:uuid:2"]
    assert items[0].title == "Atom one"
    assert items[0].body == "Atom summary one"
    assert items[0].published_at == "2026-11-11T09:30:00Z"
    assert items[1].body == "Atom body two"
    assert items[1].published_at == "2026-11-10T08:00:00Z"


def test_parse_rdf() -> None:
    items = parse_feed_items(RDF, max_text_chars=600)
    assert len(items) == 1
    assert items[0].item_key == "https://example.org/rdf/1"
    assert items[0].body == "RDF body"
    assert items[0].published_at == "2026-11-08T07:00:00Z"


def test_item_key_fallback_chain() -> None:
    items = parse_feed_items(RSS20, max_text_chars=600)
    assert items[0].item_key == "g1"
    assert items[1].item_key == "https://example.org/b"
    derived = hashlib.sha256(b"Third story\x002026-11-09T10:00:00Z").hexdigest()[:16]
    assert items[2].item_key == derived
    assert parse_feed_items(RSS20, max_text_chars=600)[2].item_key == derived


def test_dedup_first_wins() -> None:
    items = parse_feed_items(RSS20_DUP_GUID, max_text_chars=600)
    assert len(items) == 1
    assert items[0].title == "Alpha"


def test_selection_independent_of_order() -> None:
    ordered = parse_feed_items(RSS20, max_text_chars=600)
    shuffled = parse_feed_items(RSS20_REVERSED, max_text_chars=600)
    for hour in range(8):
        bucket = f"2026-11-10T{hour:02d}"
        seed = __import__("mario.rss", fromlist=["rss_bucket_seed"]).rss_bucket_seed(bucket)
        assert select_feed_item(ordered, seed) == select_feed_item(shuffled, seed)
    assert select_feed_item([], 1) is None


def test_rejects_invalid_documents() -> None:
    for payload in (BROKEN_XML, DOCTYPE_FEED, ENTITY_FEED, NOT_A_FEED, b""):
        with pytest.raises(RssParseError):
            parse_feed_items(payload, max_text_chars=600)
    assert parse_feed_items(EMPTY_FEED, max_text_chars=600) == []


# --------------------------------------------------------------------------- text


def test_strip_html_and_entities() -> None:
    raw = '<script>alert("x")</script>Hello <b>world</b> &amp; &#233;&nbsp;end'
    assert strip_html(raw) == "Hello world & é end"
    assert strip_html(strip_html(raw)) == "Hello world & é end"
    assert "<b>" not in strip_html("<b>bold</b>")
    assert strip_html("already plain") == "already plain"


def test_truncate_text_policy() -> None:
    assert truncate_text("short", 50) == "short"

    sentences = "One. Two. Three. " + "x" * 100
    cut = truncate_text(sentences, 20)
    assert cut == "One. Two. Three."
    assert len(cut) <= 20
    assert "…" not in cut

    no_terminator = "aaaa bbbb cccc dddd eeee"
    words = truncate_text(no_terminator, 10)
    assert words == "aaaa bbbb"
    assert len(words) <= 10

    assert truncate_text("abcdefghijklmno", 5) == "abcde"

    with pytest.raises(ValueError):
        truncate_text("x", 0)


def test_text_assembly_caps_body_not_whole() -> None:
    preset = RssPreset(
        template_version="mario/rss/1",
        intro="De los feeds. {title}.",
        outro="Volviendo a la música.",
        fallback="quiet",
    )
    item = FeedItem(item_key="k", title="Headline", body="y" * 40, published_at="")
    text, text_hash = build_rss_text(item, preset)
    assert text.startswith("De los feeds. Headline.")
    assert text.endswith("Volviendo a la música.")
    assert len(text) > 40
    assert text_hash == hashlib.sha256(text.encode("utf-8")).hexdigest()

    fallback_text, fallback_hash = build_rss_fallback_text(preset)
    assert fallback_text == "quiet"
    assert fallback_hash == hashlib.sha256(b"rss-fallback").hexdigest()


# --------------------------------------------------------------------------- identity


def test_cache_and_alias_filenames() -> None:
    url = "http://example.org/feed"
    name = rss_cache_filename(url, "g1", BUCKET, "abc")
    assert re.fullmatch(r"rss_[0-9a-f]{8}_2026-11-10T14\.wav", name)
    assert rss_alias_filename("hn", BUCKET) == "rss_hn_2026-11-10T14.wav"
    assert rss_fallback_filename("hn", BUCKET) == "rss_fallback_hn_2026-11-10T14.wav"
    assert re.fullmatch(r"[0-9a-f]{16}_2026-11-10T14\.xml", feed_cache_filename(url, BUCKET))
    assert rss_alias_filename("hn", "2026-11-10T14:30") == "rss_hn_2026-11-10T14-30.wav"

    key = rss_cache_key(url, "g1", BUCKET, "abc")
    assert name == f"rss_{key[:8]}_2026-11-10T14.wav"


def test_phrasing_change_changes_filename() -> None:
    item = FeedItem(item_key="g1", title="Story", body="Body text.", published_at="")
    original = RssPreset("mario/rss/1", "De los feeds. {title}.", "Volviendo a la música.", "q")
    edited = RssPreset("mario/rss/1", "Reading now. {title}.", "Volviendo a la música.", "q")

    text_a, hash_a = build_rss_text(item, original)
    text_b, hash_b = build_rss_text(item, edited)
    assert text_a != text_b
    assert hash_a != hash_b

    url = "https://example.org/feed"
    assert rss_cache_filename(url, item.item_key, BUCKET, hash_a) != rss_cache_filename(
        url, item.item_key, BUCKET, hash_b
    )


def test_preset_loader(tmp_path: Path) -> None:
    preset = load_rss_preset(Path(__file__).resolve().parents[1] / "presets" / "rss.yaml")
    assert preset.template_version == "mario/rss/1"
    assert preset.intro.format(title="X") == "De los feeds. X."
    assert preset.fallback

    bad = tmp_path / "bad.yaml"
    bad.write_text("intro: '{nope}'\nfallback: 'x'\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_rss_preset(bad)


# --------------------------------------------------------------------------- feed cache


def test_load_feed_bytes_caches_and_forces(tmp_path: Path) -> None:
    feed = FeedConfig(
        name="hn",
        url="https://example.org/feed",
        normalized_url="https://example.org/feed",
        slug="hn",
    )
    fetcher = FakeFetcher({feed.normalized_url: RSS20})

    first = load_feed_bytes(feed, tmp_path / "cache", BUCKET, fetcher=fetcher)
    assert first == RSS20
    second = load_feed_bytes(feed, tmp_path / "cache", BUCKET, fetcher=fetcher)
    assert second == RSS20
    assert len(fetcher.calls) == 1
    assert len(list((tmp_path / "cache").glob("*.xml"))) == 1

    forced = load_feed_bytes(feed, tmp_path / "cache", BUCKET, fetcher=fetcher, force=True)
    assert forced == RSS20
    assert len(fetcher.calls) == 2


# --------------------------------------------------------------------------- generate()


def test_fetch_once_and_reuse_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    tts = _install_tts(monkeypatch)
    feeds = _feeds_file(tmp_path, "- https://example.org/feed\n")
    cfg = _config(tmp_path, "once", rss_feeds_file=feeds)
    fetcher = FakeFetcher({"https://example.org/feed": RSS20})

    first = generate(cfg, time_bucket=BUCKET, feed_fetcher=fetcher)
    assert len(first) == 1
    assert tts["n"] == 1

    second = generate(cfg, time_bucket=BUCKET, feed_fetcher=fetcher)
    assert [p.name for p in second] == [p.name for p in first]
    assert len(fetcher.calls) == 1
    assert tts["n"] == 1
    assert len(list((tmp_path / "once" / "cache").glob("*.xml"))) == 1


def test_force_refetches_and_rerenders(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    tts = _install_tts(monkeypatch)
    feeds = _feeds_file(tmp_path, "- https://example.org/feed\n")
    cfg = _config(tmp_path, "force", rss_feeds_file=feeds)
    fetcher = FakeFetcher({"https://example.org/feed": RSS20})

    generate(cfg, time_bucket=BUCKET, feed_fetcher=fetcher)
    generate(cfg, time_bucket=BUCKET, force=True, feed_fetcher=fetcher)

    assert len(fetcher.calls) == 2
    assert tts["n"] == 2


def test_max_items_per_cycle_caps_aired_segments(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_tts(monkeypatch)
    feeds = _feeds_file(
        tmp_path,
        "- name: one\n  url: https://example.org/1\n"
        "- name: two\n  url: https://example.org/2\n"
        "- name: three\n  url: https://example.org/3\n",
    )
    cfg = _config(tmp_path, "cap", rss_feeds_file=feeds, rss_max_items_per_cycle=2)
    fetcher = FakeFetcher(
        {
            "https://example.org/1": RSS20,
            "https://example.org/2": RSS20,
            "https://example.org/3": RSS20,
        }
    )

    paths = generate(cfg, time_bucket=BUCKET, feed_fetcher=fetcher)

    # The cap bounds how many segments air, not how many feeds exist: fetching
    # stops as soon as it is met instead of truncating the feed list.
    assert len(fetcher.calls) == 2
    assert len([p for p in paths if p.name.startswith("rss_")]) == 2


def test_rotation_visits_every_feed_across_buckets(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_tts(monkeypatch)
    feeds = _feeds_file(
        tmp_path,
        "- name: one\n  url: https://example.org/1\n"
        "- name: two\n  url: https://example.org/2\n"
        "- name: three\n  url: https://example.org/3\n",
    )
    cfg = _config(tmp_path, "rotate", rss_feeds_file=feeds)

    aired: list[str] = []
    for hour in range(6):
        fetcher = FakeFetcher(
            {
                "https://example.org/1": RSS20,
                "https://example.org/2": RSS20,
                "https://example.org/3": RSS20,
            }
        )
        bucket = f"2026-11-{10 + hour:02d}T14"
        generate(cfg, time_bucket=bucket, feed_fetcher=fetcher)
        aired.append(fetcher.calls[0])

    # Truncating the list by position (the old behaviour) would pin one feed.
    assert len(set(aired)) > 1


def test_empty_feed_is_skipped_without_fallback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_tts(monkeypatch)
    feeds = _feeds_file(
        tmp_path,
        "- name: dead\n  url: https://example.org/dead\n"
        "- name: live\n  url: https://example.org/live\n",
    )
    cfg = _config(tmp_path, "skip", rss_feeds_file=feeds)
    fetcher = FakeFetcher(
        {"https://example.org/dead": EMPTY_FEED, "https://example.org/live": RSS20}
    )

    paths = generate(cfg, time_bucket=BUCKET, feed_fetcher=fetcher)

    # Whichever feed the rotation starts on, the dead one must not air.
    assert len(paths) == 1
    assert (cfg.host_dir / rss_alias_filename("live", BUCKET)).exists()
    assert not (cfg.host_dir / rss_alias_filename("dead", BUCKET)).exists()
    assert not list(cfg.host_dir.glob("rss_fallback_*"))


def test_stable_alias_points_at_the_aired_item(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_tts(monkeypatch)
    feeds = _feeds_file(tmp_path, "- name: hn\n  url: https://example.org/feed\n")
    cfg = _config(tmp_path, "stable", rss_feeds_file=feeds)

    paths = generate(cfg, time_bucket=BUCKET, feed_fetcher=FakeFetcher({"https://example.org/feed": RSS20}))

    stable = cfg.host_dir / rss_stable_alias_filename(BUCKET)
    target = paths[0]
    assert stable.is_symlink()
    assert os.readlink(stable) == target.name


def test_fetch_failures_stop_early(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _install_tts(monkeypatch)
    feeds = _feeds_file(
        tmp_path,
        "".join(f"- name: f{i}\n  url: https://example.org/{i}\n" for i in range(6)),
    )
    cfg = _config(tmp_path, "failfast", rss_feeds_file=feeds)
    fetcher = FakeFetcher({f"https://example.org/{i}": None for i in range(6)})

    generate(cfg, time_bucket=BUCKET, feed_fetcher=fetcher)

    # A dead network must not cost one timeout per configured feed.
    assert len(fetcher.calls) == 2


def test_fallback_on_fetch_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _install_tts(monkeypatch)
    feeds = _feeds_file(tmp_path, "- name: hn\n  url: https://example.org/feed\n")
    cfg = _config(tmp_path, "fail", rss_feeds_file=feeds)
    cfg.host_dir.mkdir(parents=True, exist_ok=True)
    stale = cfg.host_dir / rss_alias_filename("hn", BUCKET)
    stale.write_bytes(b"STALE-ITEM-AUDIO")

    fetcher = FakeFetcher({"https://example.org/feed": None})
    paths = generate(cfg, time_bucket=BUCKET, feed_fetcher=fetcher)

    assert len(paths) == 1
    assert paths[0].name == rss_fallback_filename("hn", BUCKET)
    assert paths[0].is_file()
    assert not stale.exists()


def test_fallback_on_invalid_documents(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _install_tts(monkeypatch)
    feeds = _feeds_file(tmp_path, "- name: hn\n  url: https://example.org/feed\n")
    cfg = _config(tmp_path, "invalid", rss_feeds_file=feeds)

    for payload in (BROKEN_XML, DOCTYPE_FEED, ENTITY_FEED, NOT_A_FEED, BLANK_ITEMS):
        fetcher = FakeFetcher({"https://example.org/feed": payload})
        paths = generate(cfg, time_bucket=BUCKET, force=True, feed_fetcher=fetcher)
        assert [p.name for p in paths] == [rss_fallback_filename("hn", BUCKET)]


def test_fallback_on_empty_feed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _install_tts(monkeypatch)
    feeds = _feeds_file(tmp_path, "- name: hn\n  url: https://example.org/feed\n")
    cfg = _config(tmp_path, "empty", rss_feeds_file=feeds)
    fetcher = FakeFetcher({"https://example.org/feed": EMPTY_FEED})

    paths = generate(cfg, time_bucket=BUCKET, feed_fetcher=fetcher)

    assert [p.name for p in paths] == [rss_fallback_filename("hn", BUCKET)]


def test_rss_only_empty_library(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _install_tts(monkeypatch)
    feeds = _feeds_file(tmp_path, "- name: hn\n  url: https://example.org/feed\n")
    cfg = _config(tmp_path, "only", rss_feeds_file=feeds)
    fetcher = FakeFetcher({"https://example.org/feed": RSS20})

    paths = generate(cfg, time_bucket=BUCKET, feed_fetcher=fetcher)

    assert len(paths) == 1
    assert paths[0].name.startswith("rss_")
    assert (cfg.host_dir / "current-bucket").read_text(encoding="utf-8") == BUCKET


def test_empty_library_no_rss_returns_empty(tmp_path: Path) -> None:
    cfg = _config(tmp_path, "nothing", rss_cache_dir=None)
    assert generate(cfg, time_bucket=BUCKET) == []


def test_rss_dry_run_no_fetch(tmp_path: Path) -> None:
    feeds = _feeds_file(
        tmp_path,
        "- name: one\n  url: https://example.org/1\n- name: two\n  url: https://example.org/2\n",
    )
    cfg = _config(tmp_path, "dry", rss_feeds_file=feeds, rss_max_items_per_cycle=2)

    paths = generate(cfg, time_bucket=BUCKET, dry_run=True, feed_fetcher=_blow_up)

    # Which feed has items needs a fetch, which a dry run must not do. The only
    # honest answer is the name playout will read.
    assert [p.name for p in paths] == [rss_stable_alias_filename(BUCKET)]
    assert all(not p.exists() for p in paths)
    assert not cfg.host_dir.exists()


def test_rss_determinism_across_runs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _install_tts(monkeypatch)
    feeds = _feeds_file(tmp_path, "- name: hn\n  url: https://example.org/feed\n")

    cfg_a = _config(tmp_path, "a", rss_feeds_file=feeds)
    cfg_b = _config(tmp_path, "b", rss_feeds_file=feeds)
    fetcher_a = FakeFetcher({"https://example.org/feed": RSS20})
    fetcher_b = FakeFetcher({"https://example.org/feed": RSS20})

    paths_a = generate(cfg_a, time_bucket=BUCKET, force=True, feed_fetcher=fetcher_a)
    paths_b = generate(cfg_b, time_bucket=BUCKET, force=True, feed_fetcher=fetcher_b)

    assert [p.name for p in paths_a] == [p.name for p in paths_b]
    hashes_a = [hashlib.sha256(p.read_bytes()).hexdigest() for p in paths_a]
    hashes_b = [hashlib.sha256(p.read_bytes()).hexdigest() for p in paths_b]
    assert hashes_a == hashes_b

    alias = cfg_a.host_dir / rss_alias_filename("hn", BUCKET)
    target = paths_a[0]
    if alias.is_symlink():
        assert os.readlink(alias) == target.name
    else:
        assert alias.read_bytes() == target.read_bytes()


# --------------------------------------------------------------------------- fetch module


class _FakeResponse:
    def __init__(self, body: bytes, final_url: str, status: int = 200) -> None:
        self._body = body
        self._final_url = final_url
        self.status = status

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def read(self, n: int = -1) -> bytes:
        # Deliberately ignores `n`: the caller must enforce the cap itself.
        return self._body

    def geturl(self) -> str:
        return self._final_url

    def close(self) -> None:
        return None


def test_fetch_feed_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def install(handler: Any) -> None:
        monkeypatch.setattr(urllib.request, "urlopen", handler)

    install(lambda *a, **k: _FakeResponse(b"<rss/>", "https://example.org/feed"))
    assert fetch_feed("https://example.org/feed", timeout=1.0, max_bytes=64) == b"<rss/>"

    # Non-http(s) targets never reach the opener.
    for url in ("file:///etc/passwd", "ftp://example.org/x", "example.org/feed", ""):
        install(_blow_up)
        assert fetch_feed(url) is None

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise urllib.error.URLError("dns")

    install(boom)
    assert fetch_feed("https://example.org/feed") is None

    def timeout(*args: Any, **kwargs: Any) -> Any:
        raise TimeoutError("slow")

    install(timeout)
    assert fetch_feed("https://example.org/feed") is None

    for status in (404, 500):
        install(lambda *a, s=status, **k: _FakeResponse(b"nope", "https://e.org/f", s))
        assert fetch_feed("https://e.org/f") is None

    install(lambda *a, **k: _FakeResponse(b"x" * 65, "https://e.org/f"))
    assert fetch_feed("https://e.org/f", max_bytes=64) is None

    install(lambda *a, **k: _FakeResponse(b"<rss/>", "file:///etc/passwd"))
    assert fetch_feed("https://e.org/f") is None

    install(lambda *a, **k: _FakeResponse(b"", "https://e.org/f"))
    assert fetch_feed("https://e.org/f") is None


# --------------------------------------------------------------------------- docs


def test_readme_documents_rss_network_scope() -> None:
    readme = (Path(__file__).resolve().parents[1] / "docs" / "MARIO.md").read_text(encoding="utf-8")
    assert "No network calls at generation time" not in readme
    assert "feedfetch" in readme
