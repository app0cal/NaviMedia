from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse


class Source(StrEnum):
    YOUTUBE = "youtube"
    SPOTIFY = "spotify"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ClassifiedUrl:
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
    if path != "/" and path.endswith("/"):
        return path.rstrip("/")
    return path
