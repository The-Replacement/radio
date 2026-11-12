"""The single network egress of Mario: fetch raw RSS feed payloads.

Every other module is local. This one is deliberately tiny and defensive:
it enforces the scheme allow-list, a hard byte cap, and a timeout, and it
returns ``None`` on any failure instead of raising, so a bad feed can never
break a generation cycle.
"""

from __future__ import annotations

import re
import urllib.error
import urllib.request

from .rss import DEFAULT_RSS_MAX_BYTES

_SCHEME_RE = re.compile(r"^([A-Za-z][A-Za-z0-9+.\-]*)://")
_ALLOWED_SCHEMES: tuple[str, ...] = ("http", "https")
_USER_AGENT: str = "mario-rss/1"


def _scheme_of(url: str) -> str | None:
    match = _SCHEME_RE.match(url.strip())
    if match is None:
        return None
    return match.group(1).lower()


def fetch_feed(
    url: str,
    *,
    timeout: float = 10.0,
    max_bytes: int = DEFAULT_RSS_MAX_BYTES,
) -> bytes | None:
    """Fetch ``url`` and return its bytes, or ``None`` on any failure.

    Never raises, including for malformed URLs, timeouts, non-2xx statuses,
    oversized payloads, and redirects that leave the http/https allow-list.
    """
    try:
        target = url.strip()
        if max_bytes < 1 or timeout <= 0:
            return None
        if _scheme_of(target) not in _ALLOWED_SCHEMES:
            return None
        request = urllib.request.Request(target, headers={"User-Agent": _USER_AGENT})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = getattr(response, "status", None)
            if status is None:
                getter = getattr(response, "getcode", None)
                status = getter() if callable(getter) else 200
            if status is not None and not 200 <= int(status) < 300:
                return None
            final_url = ""
            geturl = getattr(response, "geturl", None)
            if callable(geturl):
                final_url = geturl() or ""
            if _scheme_of(final_url or target) not in _ALLOWED_SCHEMES:
                return None
            data = response.read(max_bytes + 1)
        if not isinstance(data, (bytes, bytearray)):
            return None
        payload = bytes(data)
        if not payload or len(payload) > max_bytes:
            return None
        return payload
    except Exception:  # noqa: BLE001 - defensive by contract: never raise
        return None
