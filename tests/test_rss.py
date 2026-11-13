"""Tests for the RSS reader (The Reader)."""

from __future__ import annotations

import hashlib
import json
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
    rss_cache_key,
    select_feed_item,
    strip_html,
    truncate_text,
)
from tests.conftest import install_fake_kokoro

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


def _kokoro_files(tmp_path: Path) -> tuple[Path, Path]:
    model = tmp_path / "kokoro.onnx"
    voices = tmp_path / "voices.bin"
    model.write_bytes(b"m")
    voices.write_bytes(b"v")
    return model, voices


def _config(tmp_path: Path, name: str, **overrides: Any) -> MarioConfig:
    base = tmp_path / name
    model, voices = _kokoro_files(tmp_path)
    kwargs: dict[str, Any] = {
        "music_dir": base / "music",
        "host_dir": base / "host",
        "model": model,
        "voices_path": voices,
        "rss_cache_dir": base / "cache",
    }
    kwargs.update(overrides)
    return MarioConfig(**kwargs)


def _feeds_file(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "feeds.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def _sidecar(music_dir: Path, seed: int) -> None:
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
    calls = install_fake_kokoro(monkeypatch)
    music = tmp_path / "music"
    _sidecar(music, 1)
    _sidecar(music, 2)
    base = tmp_path / "off"
    model, voices = _kokoro_files(tmp_path)
    cfg = MarioConfig(music_dir=music, host_dir=base / "host", model=model, voices_path=voices, rss_cache_dir=None)

    paths = generate(cfg, time_bucket=BUCKET, feed_fetcher=_blow_up)

    assert calls["n"] > 0
    assert not [p.name for p in paths if p.name.startswith("rss_")]
    assert not [p.name for p in cfg.host_dir.glob("rss_*")]
    assert not (cfg.host_dir.parent / "rss-cache").exists()


# --------------------------------------------------------------------------- config file / parsing / fetch / gen tests (preserved/adapted)


def test_load_rss_feeds_variants(tmp_path: Path) -> None:
    bare = _feeds_file(tmp_path, "- https://example.org/one.xml\n- https://example.org/two.xml\n")
    feeds = load_rss_feeds(bare)
    assert [f.normalized_url for f in feeds] == ["https://example.org/one.xml", "https://example.org/two.xml"]
    assert all(f.name is None for f in feeds)
    assert [f.slug for f in feeds] == ["example-org-one-xml", "example-org-two-xml"]

    named_path = tmp_path / "named.yaml"
    named_path.write_text("- name: hn\n  url: https://news.example.org/rss\n- https://example.org/plain\n", encoding="utf-8")
    named = load_rss_feeds(named_path)
    assert named[0].name == "hn"
    assert named[0].slug == "hn"
    assert named[1].slug == "example-org-plain"

    mapping = tmp_path / "mapping.yaml"
    mapping.write_text("feeds:\n  - name: atomic\n    url: https://example.org/atom.xml?x=1\n", encoding="utf-8")
    mapped = load_rss_feeds(mapping)
    assert len(mapped) == 1
    assert mapped[0].normalized_url == "https://example.org/atom.xml?x=1"


def test_load_rss_feeds_rejects_invalid(tmp_path: Path) -> None:
    duplicate = _feeds_file(tmp_path, "- https://example.org/feed.xml\n- https://example.org/feed.xml#top\n")
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
    assert normalize_feed_url("http://example.org/P") == normalize_feed_url("http://example.org/P")
    assert normalize_feed_url(normalize_feed_url("https://Example.org/f#frag")) == "https://example.org/f"
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
    assert select_feed_item(parse_feed_items(RSS20, max_text_chars=600), 42) == select_feed_item(
        parse_feed_items(RSS20_REVERSED, max_text_chars=600), 42
    )


@pytest.mark.parametrize(
    "raw",
    [BROKEN_XML, DOCTYPE_FEED, ENTITY_FEED, NOT_A_FEED],
    ids=["broken", "doctype", "entity", "not-feed"],
)
def test_invalid_feeds_rejected(raw: bytes) -> None:
    with pytest.raises(RssParseError):
        parse_feed_items(raw, max_text_chars=600)


def test_strip_and_truncate() -> None:
    assert strip_html('<div>hello <b>world</b> &amp; friends<script>x</script></div>') == "hello world & friends"
    assert strip_html("  spaced   apart  ") == "spaced apart"
    assert truncate_text("  short  text  ", 100) == "  short  text  "
    clipped = truncate_text("alpha beta gamma delta", 16)
    assert len(clipped) <= 16
    assert not clipped.endswith(" ")
    assert truncate_text("alpha beta", 5) == "alpha"


def test_preset_and_build_text() -> None:
    preset = load_rss_preset(Path("presets/rss.yaml"))
    assert preset.template_version == "mario/rss/1"
    assert "{title}" in preset.intro
    assert preset.outro
    assert preset.fallback

    item = FeedItem(item_key="k", title="News", body="A & B", published_at="")
    text, h = build_rss_text(item, preset)
    assert "News" in text
    assert "A & B" in text
    assert h == hashlib.sha256(text.encode("utf-8")).hexdigest()

    fallback, fh = build_rss_fallback_text(preset)
    assert fallback == preset.fallback
    assert isinstance(fh, str) and len(fh) == 64

    original = RssPreset("mario/rss/1", "De los feeds. {title}.", "Volviendo a la música.", "q")
    edited = RssPreset("mario/rss/1", "Reading now. {title}.", "Volviendo a la música.", "q")
    assert build_rss_text(item, original)[1] != build_rss_text(item, edited)[1]


def test_fetch_cache_and_force(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    feed = load_rss_feeds(_feeds_file(tmp_path, "- https://example.org/a.xml\n"))[0]
    fetcher = FakeFetcher({"https://example.org/a.xml": RSS20})

    first = load_feed_bytes(feed, tmp_path / "cache", BUCKET, fetcher=fetcher)
    assert first == RSS20
    cached = tmp_path / "cache" / feed_cache_filename(feed.normalized_url, BUCKET)
    assert cached.read_bytes() == RSS20
    assert fetcher.calls == ["https://example.org/a.xml"]

    second = load_feed_bytes(feed, tmp_path / "cache", BUCKET, fetcher=_blow_up)
    assert second == RSS20

    updated = FakeFetcher({"https://example.org/a.xml": RSS20_REVERSED})
    third = load_feed_bytes(feed, tmp_path / "cache", BUCKET, fetcher=updated, force=True)
    assert third == RSS20_REVERSED
    assert cached.read_bytes() == RSS20_REVERSED
    assert updated.calls == ["https://example.org/a.xml"]


class _FakeResponse:
    def __init__(self, body: bytes) -> None:
        self._body = body
        self.offset = 0

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            size = len(self._body) - self.offset
        chunk = self._body[self.offset : self.offset + size]
        self.offset += len(chunk)
        return chunk


def test_fetch_feed_uses_urllib_and_caps(monkeypatch: pytest.MonkeyPatch) -> None:
    opened: list[Any] = []

    def fake_urlopen(request: urllib.request.Request, timeout: float = 0.0) -> _FakeResponse:
        opened.append(request)
        return _FakeResponse(RSS20)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert fetch_feed("https://example.org/feed.xml", timeout=3.0, max_bytes=1024) == RSS20
    assert len(opened) == 1
    assert opened[0].full_url == "https://example.org/feed.xml"
    assert opened[0].headers.get("User-agent") == "mario-rss/1"

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _FakeResponse(b"x" * 64))
    assert fetch_feed("https://example.org/feed.xml", max_bytes=8) is None

    def boom(*a: Any, **k: Any) -> None:
        raise urllib.error.URLError("nope")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    assert fetch_feed("https://example.org/feed.xml") is None


# --------------------------------------------------------------------------- generate / rendering behavior (adapted for Kokoro)


def _rss_cfg(tmp_path: Path, feeds_body: str, **overrides: Any) -> MarioConfig:
    cfg = _config(tmp_path, "rss", **overrides)
    cfg.rss_feeds_file = _feeds_file(tmp_path, feeds_body)
    return cfg


def test_generate_rss_cycle_cache_hit(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls = install_fake_kokoro(monkeypatch)
    cfg = _rss_cfg(tmp_path, "- https://example.org/feed.xml\n")
    fetcher = FakeFetcher({"https://example.org/feed.xml": RSS20})

    first = generate(cfg, time_bucket=BUCKET, feed_fetcher=fetcher)
    assert any(p.name.startswith("rss_") for p in first)
    n1 = calls["n"]
    assert fetcher.calls == ["https://example.org/feed.xml"]

    second = generate(cfg, time_bucket=BUCKET, feed_fetcher=fetcher)
    assert [p.name for p in second] == [p.name for p in first]
    assert calls["n"] == n1  # cached render+fetch
    assert fetcher.calls == ["https://example.org/feed.xml"]


def test_force_refetches_and_rerenders(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls = install_fake_kokoro(monkeypatch)
    cfg = _rss_cfg(tmp_path, "- https://example.org/feed.xml\n")
    fetcher = FakeFetcher({"https://example.org/feed.xml": RSS20})
    generate(cfg, time_bucket=BUCKET, feed_fetcher=fetcher)
    n1 = calls["n"]
    updated = FakeFetcher({"https://example.org/feed.xml": RSS20_REVERSED})
    generate(cfg, time_bucket=BUCKET, force=True, feed_fetcher=updated)
    assert updated.calls == ["https://example.org/feed.xml"]
    assert calls["n"] > n1


def test_rss_only_empty_library(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    install_fake_kokoro(monkeypatch)
    cfg = _rss_cfg(tmp_path, "- https://example.org/feed.xml\n")
    fetcher = FakeFetcher({"https://example.org/feed.xml": RSS20})
    paths = generate(cfg, time_bucket=BUCKET, feed_fetcher=fetcher)
    assert paths
    assert any(p.name.startswith("rss_") for p in paths)


def test_rss_determinism_across_runs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    install_fake_kokoro(monkeypatch, marker=b"DETERMINISTIC-RSS")
    feeds = "- https://example.org/feed.xml\n"
    cfg_a = _rss_cfg(tmp_path, feeds)
    cfg_b = _rss_cfg(tmp_path, feeds)
    fetcher_a = FakeFetcher({"https://example.org/feed.xml": RSS20})
    fetcher_b = FakeFetcher({"https://example.org/feed.xml": RSS20})
    p1 = generate(cfg_a, time_bucket=BUCKET, force=True, feed_fetcher=fetcher_a)
    p2 = generate(cfg_b, time_bucket=BUCKET, force=True, feed_fetcher=fetcher_b)
    assert [p.name for p in p1] == [p.name for p in p2]
    hashes1 = sorted(hashlib.sha256(p.read_bytes()).hexdigest() for p in p1)
    hashes2 = sorted(hashlib.sha256(p.read_bytes()).hexdigest() for p in p2)
    assert hashes1 == hashes2
