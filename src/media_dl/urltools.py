"""Classify supported media URLs and normalize them for dedupe."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse


class Source(StrEnum):
    """Name the media sources the system can route to downloaders."""

    YOUTUBE = "youtube"
    SPOTIFY = "spotify"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ClassifiedUrl:
    """Carry the raw URL, normalized URL, and detected source together."""

    raw_url: str
    normalized_url: str
    source: Source


YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
}

SPOTIFY_HOSTS = {
    "open.spotify.com",
    "spotify.link",
}


def classify_url(raw_url: str) -> ClassifiedUrl:
    """Return the source classification and normalized form for a submitted URL."""
    stripped = raw_url.strip()
    normalized = normalize_url(stripped)
    host = urlparse(normalized).netloc.lower()

    if host in YOUTUBE_HOSTS:
        source = Source.YOUTUBE
    elif host in SPOTIFY_HOSTS:
        source = Source.SPOTIFY
    else:
        source = Source.UNKNOWN

    return ClassifiedUrl(raw_url=stripped, normalized_url=normalized, source=source)


def normalize_url(raw_url: str) -> str:
    """Normalize URL casing, tracking parameters, sorting, and path suffixes."""
    parsed = urlparse(raw_url.strip())
    scheme = parsed.scheme.lower() or "https"
    host = parsed.netloc.lower()
    path = _normalize_path(parsed.path)

    query_items = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        lower_key = key.lower()
        if lower_key.startswith("utm_") or lower_key in {"si", "feature", "pp"}:
            continue
        query_items.append((key, value))

    query = urlencode(sorted(query_items))
    return urlunparse((scheme, host, path, "", query, ""))


def _normalize_path(path: str) -> str:
    """Trim trailing slashes except for the root path."""
    if path != "/" and path.endswith("/"):
        return path.rstrip("/")
    return path
