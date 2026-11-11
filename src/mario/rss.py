"""RSS feed reading, deterministic selection, and spoken text for The Reader.

This module is pure by design: it never opens a connection and never runs a
subprocess. It consumes feed bytes that someone else obtained (see
``mario.feedfetch``) and owns everything that must be reproducible:
parsing, normalization, deterministic selection, hashing, cache names, and the
disk cache of raw feed payloads.

Determinism contract: the same feed bytes, time bucket, config, and preset
produce the same selected item, the same spoken text, the same cache filename,
and therefore the same audio.
"""

from __future__ import annotations

import hashlib
import html
import logging
import os
import re
import xml.etree.ElementTree as ET
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path

import yaml  # type: ignore[import-untyped]

from .timebucket import sanitize_bucket

logger = logging.getLogger(__name__)

DEFAULT_RSS_MAX_BYTES: int = 5 * 1024 * 1024
MAX_FEED_ITEMS: int = 1000

FALLBACK_RSS_TEXT_HASH: str = hashlib.sha256(b"rss-fallback").hexdigest()

FeedFetcher = Callable[[str], bytes | None]

_ALLOWED_SCHEMES: tuple[str, ...] = ("http", "https")
_ROOT_KINDS: tuple[str, ...] = ("rss", "rdf", "feed")
_ITEM_KINDS: tuple[str, ...] = ("item", "entry")
_BODY_PRIORITY: tuple[str, ...] = ("description", "encoded", "summary", "content")
_PUBLISHED_PRIORITY: tuple[str, ...] = ("pubdate", "published", "updated", "date")
_SENTENCE_TERMINATORS: str = ".!?…"

_SCHEME_RE = re.compile(r"^([A-Za-z][A-Za-z0-9+.\-]*)://(.*)$", re.DOTALL)
_SLUG_RE = re.compile(r"[^a-z0-9]+")
_SCRIPT_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1\s*>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]*>")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_WS_RE = re.compile(r"\s+")
_DTD_RE = re.compile(rb"<!\s*(?:DOCTYPE|ENTITY)", re.IGNORECASE)
_PLACEHOLDER_RE = re.compile(r"\{([^{}]*)\}")


@dataclass(frozen=True)
class FeedConfig:
    """One operator-configured feed."""

    name: str | None
    url: str
    normalized_url: str
    slug: str


@dataclass(frozen=True)
class FeedItem:
    """One parsed feed item, already normalized for speech."""

    item_key: str
    title: str
    body: str
    published_at: str


@dataclass(frozen=True)
class RssPreset:
    """Spoken phrasing for the RSS segment."""

    template_version: str
    intro: str
    outro: str
    fallback: str


class RssParseError(ValueError):
    """Raised when feed bytes cannot be parsed into items."""


def _split_url(url: str) -> tuple[str, str, str]:
    """Split ``url`` into (scheme, authority, rest). Returns empty scheme when absent."""
    match = _SCHEME_RE.match(url)
    if match is None:
        return "", "", url
    scheme = match.group(1)
    remainder = match.group(2)
    cut = len(remainder)
    for marker in "/?#":
        idx = remainder.find(marker)
        if idx != -1 and idx < cut:
            cut = idx
    return scheme, remainder[:cut], remainder[cut:]


def _lower_host(authority: str) -> str:
    """Lowercase the host portion of an authority, keeping userinfo and port intact."""
    userinfo = ""
    hostport = authority
    if "@" in authority:
        userinfo, hostport = authority.rsplit("@", 1)
        userinfo = userinfo + "@"
    if hostport.startswith("["):
        end = hostport.find("]")
        if end != -1:
            inner = hostport[1:end].lower()
            return f"{userinfo}[{inner}]{hostport[end + 1:]}"
        return userinfo + hostport.lower()
    if ":" in hostport:
        host, port = hostport.rsplit(":", 1)
        return f"{userinfo}{host.lower()}:{port}"
    return userinfo + hostport.lower()


def normalize_feed_url(url: str) -> str:
    """Canonical feed URL: lowercase scheme/host, no fragment, path and query untouched."""
    raw = url.strip()
    if not raw:
        raise ValueError("feed url must be non-empty")
    raw = raw.split("#", 1)[0]
    scheme, authority, rest = _split_url(raw)
    scheme = scheme.lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise ValueError(f"feed url must use http or https, got {scheme or 'no scheme'!r}")
    if not authority:
        raise ValueError("feed url must include a host")
    return f"{scheme}://{_lower_host(authority)}{rest}"


def feed_slug(name: str | None, normalized_url: str) -> str:
    """Stable filesystem-safe slug for a feed, unique per config file."""
    source = (name or "").strip()
    if not source:
        _scheme, authority, rest = _split_url(normalized_url)
        hostport = authority.rsplit("@", 1)[-1]
        source = f"{hostport}{rest}"
    slug = _SLUG_RE.sub("-", source.lower()).strip("-")
    slug = slug[:32].strip("-")
    return slug or "feed"


def _clean_text_once(raw: str) -> str:
    text = _SCRIPT_RE.sub(" ", raw)
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    text = _TAG_RE.sub(" ", text)
    text = _CONTROL_RE.sub("", text)
    return _WS_RE.sub(" ", text).strip()


def strip_html(raw: str) -> str:
    """Turn feed markup into plain spoken text. Idempotent."""
    text = raw
    for _ in range(4):
        cleaned = _clean_text_once(text)
        if cleaned == text:
            break
        text = cleaned
    return text


def truncate_text(text: str, max_chars: int) -> str:
    """Cap ``text`` at ``max_chars`` without inventing characters."""
    if max_chars < 1:
        raise ValueError("max_chars must be >= 1")
    if len(text) <= max_chars:
        return text
    window = text[:max_chars]
    floor_index = int(0.6 * max_chars)
    for index in range(len(window) - 1, floor_index - 1, -1):
        if window[index] in _SENTENCE_TERMINATORS:
            return window[: index + 1].rstrip()
    space = window.rfind(" ")
    if space > 0:
        return window[:space].rstrip()
    return window


def _normalize_published(raw: str) -> str:
    """Normalize a feed timestamp to ``YYYY-MM-DDTHH:MM:SSZ``; empty when unparseable."""
    value = raw.strip()
    if not value:
        return ""
    stamp: datetime | None = None
    iso = value[:-1] + "+00:00" if value[-1] in ("Z", "z") else value
    try:
        stamp = datetime.fromisoformat(iso)
    except ValueError:
        stamp = None
    if stamp is None:
        try:
            stamp = parsedate_to_datetime(value)
        except (TypeError, ValueError, IndexError):
            stamp = None
    if stamp is None:
        return ""
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    return stamp.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _local_name(tag: object) -> str:
    if not isinstance(tag, str):
        return ""
    if tag.startswith("{"):
        return tag.rsplit("}", 1)[-1]
    return tag


def _child_fields(element: ET.Element) -> dict[str, tuple[ET.Element, str]]:
    """Map local child name -> (element, joined text). First occurrence wins."""
    fields: dict[str, tuple[ET.Element, str]] = {}
    for child in element:
        name = _local_name(child.tag).lower()
        if not name or name in fields:
            continue
        fields[name] = (child, "".join(child.itertext()))
    return fields


def _resolve_item_key(
    fields: dict[str, tuple[ET.Element, str]],
    title: str,
    published_raw: str,
) -> str:
    for key in ("guid", "id"):
        entry = fields.get(key)
        if entry is not None:
            candidate = entry[1].strip()
            if candidate:
                return candidate
    link = fields.get("link")
    if link is not None:
        element, text = link
        candidate = text.strip()
        if candidate:
            return candidate
        href = element.get("href")
        if href is not None and href.strip():
            return href.strip()
    seed = f"{title}\0{published_raw.strip()}"
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]


def _parse_item(element: ET.Element, max_text_chars: int) -> FeedItem | None:
    fields = _child_fields(element)
    title = truncate_text(strip_html(fields.get("title", (element, ""))[1]), max_text_chars)
    body = ""
    for key in _BODY_PRIORITY:
        entry = fields.get(key)
        if entry is None:
            continue
        candidate = entry[1]
        if candidate.strip():
            body = truncate_text(strip_html(candidate), max_text_chars)
            break
    if not body:
        body = title
    if not body:
        return None
    published_raw = ""
    for key in _PUBLISHED_PRIORITY:
        entry = fields.get(key)
        if entry is None:
            continue
        candidate = entry[1]
        if candidate.strip():
            published_raw = candidate
            break
    return FeedItem(
        item_key=_resolve_item_key(fields, title, published_raw),
        title=title,
        body=body,
        published_at=_normalize_published(published_raw),
    )


def parse_feed_items(raw: bytes, *, max_text_chars: int) -> list[FeedItem]:
    """Parse RSS 2.0, RSS 1.0 (RDF), or Atom bytes into normalized items."""
    if not raw or not raw.strip():
        raise RssParseError("feed payload is empty")
    if _DTD_RE.search(raw):
        raise RssParseError("feed payload declares a DTD or entity")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise RssParseError(f"feed payload is not well-formed XML: {exc}") from exc
    root_kind = _local_name(root.tag).lower()
    if root_kind not in _ROOT_KINDS:
        raise RssParseError(f"unexpected feed root element: {root_kind or 'unknown'}")

    elements: list[ET.Element] = []
    for element in root.iter():
        if _local_name(element.tag).lower() in _ITEM_KINDS:
            elements.append(element)
            if len(elements) >= MAX_FEED_ITEMS:
                break

    items: list[FeedItem] = []
    seen: set[str] = set()
    for element in elements:
        item = _parse_item(element, max_text_chars)
        if item is None or item.item_key in seen:
            continue
        seen.add(item.item_key)
        items.append(item)
    return items


def rss_bucket_seed(bucket: str) -> int:
    """Deterministic seed derived from the time bucket (Mario has no separate seed)."""
    return int.from_bytes(hashlib.sha256(bucket.encode("utf-8")).digest()[:8], "big")


def select_feed_item(items: Sequence[FeedItem], seed: int) -> FeedItem | None:
    """Pick one item deterministically, independent of feed document order."""
    if not items:
        return None
    ordered = sorted(items, key=lambda item: (item.published_at or "", item.item_key))
    return ordered[seed % len(ordered)]


def build_rss_text(item: FeedItem, preset: RssPreset) -> tuple[str, str]:
    """Return (spoken text, text hash) for a selected item."""
    parts = [preset.intro.format(title=item.title), item.body, preset.outro]
    text = _WS_RE.sub(" ", " ".join(part for part in parts if part.strip())).strip()
    return text, hashlib.sha256(text.encode("utf-8")).hexdigest()


def build_rss_fallback_text(preset: RssPreset) -> tuple[str, str]:
    """Return (fallback text, sentinel hash) so fallback audio is byte-stable."""
    return preset.fallback, FALLBACK_RSS_TEXT_HASH


def rss_cache_key(normalized_url: str, item_key: str, bucket: str, text_hash: str) -> str:
    """Full cache identity: feed URL, item key, bucket, and spoken text."""
    blob = f"{normalized_url}\0{item_key}\0{bucket}\0{text_hash}"
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def rss_cache_filename_from_key(cache_key: str, bucket: str) -> str:
    """Cache filename from a full cache key. The single naming authority."""
    return f"rss_{cache_key[:8]}_{sanitize_bucket(bucket)}.wav"


def rss_cache_filename(normalized_url: str, item_key: str, bucket: str, text_hash: str) -> str:
    key = rss_cache_key(normalized_url, item_key, bucket, text_hash)
    return rss_cache_filename_from_key(key, bucket)


def rss_alias_filename(slug: str, bucket: str) -> str:
    """Stable per-feed playout name for the current item."""
    return f"rss_{slug}_{sanitize_bucket(bucket)}.wav"


def rss_fallback_filename(slug: str, bucket: str) -> str:
    """Stable per-feed playout name for the fallback segment."""
    return f"rss_fallback_{slug}_{sanitize_bucket(bucket)}.wav"


def feed_cache_filename(normalized_url: str, bucket: str) -> str:
    """Raw feed payload cache name, scoped per feed and bucket."""
    digest = hashlib.sha256(normalized_url.encode("utf-8")).hexdigest()[:16]
    return f"{digest}_{sanitize_bucket(bucket)}.xml"


def load_rss_preset(path: Path) -> RssPreset:
    """Load RSS phrasing. Raises on a malformed preset (operator error)."""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise TypeError("rss preset must be a mapping")
    version = str(data.get("template_version", "mario/rss/1")).strip()
    intro = str(data.get("intro", "")).strip()
    outro = str(data.get("outro", "")).strip()
    fallback = str(data.get("fallback", "")).strip()
    if not version:
        raise ValueError("rss preset requires a non-empty 'template_version'")
    if not intro:
        raise ValueError("rss preset requires a non-empty 'intro'")
    if not fallback:
        raise ValueError("rss preset requires a non-empty 'fallback'")
    placeholders = set(_PLACEHOLDER_RE.findall(intro))
    if placeholders - {"title"}:
        raise ValueError("rss preset 'intro' may only use the {title} field")
    try:
        intro.format(title="x")
    except (IndexError, KeyError, ValueError) as exc:
        raise ValueError(f"rss preset 'intro' is not formattable: {exc}") from exc
    return RssPreset(template_version=version, intro=intro, outro=outro, fallback=fallback)


def load_rss_feeds(path: Path) -> tuple[FeedConfig, ...]:
    """Load operator feed configuration. Raises on any config mistake (loud)."""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    entries: object
    if isinstance(data, dict):
        entries = data.get("feeds")
        if not isinstance(entries, list):
            raise TypeError("rss feeds file must contain a 'feeds' list")
    elif isinstance(data, list):
        entries = data
    else:
        raise TypeError("rss feeds file must be a list or a mapping with 'feeds'")
    if not entries:
        raise ValueError("rss feeds file contains no feeds")

    feeds: list[FeedConfig] = []
    slugs: dict[str, int] = {}
    for index, entry in enumerate(entries, start=1):
        name: str | None = None
        url: str
        if isinstance(entry, str):
            url = entry
        elif isinstance(entry, dict):
            raw_url = entry.get("url")
            if not isinstance(raw_url, str) or not raw_url.strip():
                raise ValueError(f"feed entry {index} is missing a 'url'")
            url = raw_url
            raw_name = entry.get("name")
            if raw_name is not None:
                cleaned = str(raw_name).strip()
                name = cleaned or None
        else:
            raise TypeError(f"feed entry {index} must be a URL string or a mapping")
        normalized = normalize_feed_url(url)
        slug = feed_slug(name, normalized)
        if slug in slugs:
            raise ValueError(
                f"duplicate feed slug {slug!r} (entries {slugs[slug]} and {index})"
            )
        slugs[slug] = index
        feeds.append(
            FeedConfig(name=name, url=url.strip(), normalized_url=normalized, slug=slug)
        )
    return tuple(feeds)


def load_feed_bytes(
    feed: FeedConfig,
    cache_dir: Path,
    bucket: str,
    *,
    fetcher: FeedFetcher,
    force: bool = False,
) -> bytes | None:
    """Return feed bytes from the bucket cache, fetching at most once per (feed, bucket)."""
    cache_dir = Path(cache_dir)
    cache_path = cache_dir / feed_cache_filename(feed.normalized_url, bucket)
    if not force and cache_path.is_file() and cache_path.stat().st_size > 0:
        try:
            return cache_path.read_bytes()
        except OSError as exc:
            logger.warning(
                "rss: cached payload unreadable for %s (%s); fetching again", feed.slug, exc
            )
    try:
        raw = fetcher(feed.normalized_url)
    except Exception as exc:  # noqa: BLE001 - a fetch must never break the cycle
        # Deliberately log only the error type: exception text can echo the URL,
        # and feed URLs are operator config that must not reach logs (C-15).
        logger.warning("rss: fetch failed for %s (%s)", feed.slug, type(exc).__name__)
        return None
    if raw is None:
        return None
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        tmp = cache_path.parent / (cache_path.name + ".tmp")
        tmp.write_bytes(raw)
        with tmp.open("rb") as handle:
            os.fsync(handle.fileno())
        os.replace(tmp, cache_path)
    except OSError as exc:
        # Degraded, not fatal: we still have the bytes for this run.
        logger.warning("rss: cache write failed for %s (%s); continuing", feed.slug, exc)
    return raw
