"""Extract and normalize YouTube playlist children for parent/child job expansion."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from media_dl.urltools import Source, classify_url


@dataclass(frozen=True)
class PlaylistItems:
    """Represent extracted child URLs plus entries that could not be parsed."""

    urls: list[str]
    error_count: int


def is_youtube_playlist_url(url: str) -> bool:
    """Return whether a URL is a YouTube or YouTube Music playlist-like URL."""
    classified = classify_url(url)
    if classified.source != Source.YOUTUBE:
        return False

    parsed = urlparse(classified.normalized_url)
    query = parse_qs(parsed.query)
    return parsed.path == "/playlist" or bool(query.get("list"))


def extract_youtube_playlist_items(yt_dlp_bin: str, playlist_url: str) -> PlaylistItems:
    """Run yt-dlp flat extraction and parse playlist child URLs."""
    completed = subprocess.run(
        [yt_dlp_bin, "--flat-playlist", "--dump-single-json", playlist_url],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stdout.strip() or "yt-dlp playlist extraction failed")

    return parse_youtube_playlist_json(completed.stdout)


def parse_youtube_playlist_json(payload: str) -> PlaylistItems:
    """Parse yt-dlp playlist JSON into deduped normalized child video URLs."""
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ValueError("yt-dlp returned invalid playlist JSON") from exc

    entries = data.get("entries") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        raise ValueError("yt-dlp playlist JSON did not include entries")

    urls: list[str] = []
    error_count = 0
    seen: set[str] = set()
    for entry in entries:
        url = _url_for_entry(entry)
        if url is None:
            error_count += 1
            continue
        if url in seen:
            continue
        seen.add(url)
        urls.append(url)

    return PlaylistItems(urls=urls, error_count=error_count)


def _url_for_entry(entry: object) -> str | None:
    """Return the best normalized URL from one yt-dlp flat playlist entry."""
    if not isinstance(entry, dict):
        return None

    candidates = [
        entry.get("webpage_url"),
        entry.get("url"),
        entry.get("id"),
    ]
    for candidate in candidates:
        if not isinstance(candidate, str) or not candidate.strip():
            continue
        url = _normalize_entry_url(candidate.strip())
        if url is not None:
            return url
    return None


def _normalize_entry_url(value: str) -> str | None:
    """Convert a flat playlist id, relative watch path, or YouTube URL into a child URL."""
    parsed = urlparse(value)
    if parsed.scheme and parsed.netloc:
        classified = classify_url(value)
        if classified.source != Source.YOUTUBE:
            return None
        video_url = _video_url_from_watch_url(classified.normalized_url)
        return video_url or classified.normalized_url

    video_id = value
    if value.startswith("/watch"):
        query = parse_qs(urlparse(value).query)
        ids = query.get("v")
        video_id = ids[0] if ids else ""

    if not video_id or any(char.isspace() for char in video_id):
        return None

    query = urlencode({"v": video_id})
    return urlunparse(("https", "www.youtube.com", "/watch", "", query, ""))


def _video_url_from_watch_url(url: str) -> str | None:
    """Strip playlist query data from a YouTube watch URL when a video id is present."""
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    video_ids = query.get("v")
    if not video_ids:
        return None
    return urlunparse(
        (
            "https",
            "www.youtube.com",
            "/watch",
            "",
            urlencode({"v": video_ids[0]}),
            "",
        )
    )
