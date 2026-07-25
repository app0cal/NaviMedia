"""Extract and normalize YouTube playlist children for parent/child job expansion."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from media_dl.urltools import Source, classify_url


@dataclass(frozen=True)
class PlaylistEntry:
    """Represent one normalized provider item from a playlist inventory."""

    source: Source
    provider_id: str
    url: str
    title: str
    artist: str
    position: int


@dataclass(frozen=True)
class PlaylistItems:
    """Represent a playlist snapshot plus entries that could not be parsed."""

    urls: list[str]
    error_count: int
    entries: list[PlaylistEntry] = field(default_factory=list)
    title: str | None = None


def is_youtube_playlist_url(url: str) -> bool:
    """Return whether a URL is a YouTube or YouTube Music playlist-like URL."""
    classified = classify_url(url)
    if classified.source != Source.YOUTUBE:
        return False

    parsed = urlparse(classified.normalized_url)
    query = parse_qs(parsed.query)
    return parsed.path == "/playlist" or bool(query.get("list"))


def is_spotify_playlist_url(url: str) -> bool:
    """Return whether a URL is an explicit open.spotify.com playlist URL."""
    classified = classify_url(url)
    if classified.source != Source.SPOTIFY:
        return False
    parsed = urlparse(classified.normalized_url)
    parts = [part for part in parsed.path.split("/") if part]
    return len(parts) >= 2 and parts[0] == "playlist"


def is_playlist_url(url: str) -> bool:
    """Return whether a supported URL identifies a playlist."""
    return is_youtube_playlist_url(url) or is_spotify_playlist_url(url)


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
    normalized_entries: list[PlaylistEntry] = []
    error_count = 0
    seen: set[str] = set()
    for fallback_position, entry in enumerate(entries, start=1):
        url = _url_for_entry(entry)
        if url is None:
            error_count += 1
            continue
        if url in seen:
            continue
        seen.add(url)
        urls.append(url)
        normalized_entries.append(
            PlaylistEntry(
                source=Source.YOUTUBE,
                provider_id=_youtube_provider_id(entry, url),
                url=url,
                title=_text(entry, "title") or "Unknown title",
                artist=(
                    _text(entry, "artist")
                    or _text(entry, "uploader")
                    or _text(entry, "channel")
                    or "Unknown artist"
                ),
                position=_integer(entry, "playlist_index") or fallback_position,
            )
        )

    return PlaylistItems(
        urls=urls,
        error_count=error_count,
        entries=normalized_entries,
        title=_text(data, "title"),
    )


def extract_spotify_playlist_items(spotdl_bin: str, playlist_url: str) -> PlaylistItems:
    """Run spotDL metadata-only extraction for a Spotify playlist."""
    completed = subprocess.run(
        [spotdl_bin, "save", playlist_url, "--save-file", "-"],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stdout.strip() or "spotDL playlist extraction failed")
    return parse_spotify_playlist_json(completed.stdout)


def parse_spotify_playlist_json(payload: str) -> PlaylistItems:
    """Parse spotDL save output into normalized Spotify playlist items."""
    data = _json_array_from_output(payload)
    urls: list[str] = []
    entries: list[PlaylistEntry] = []
    seen: set[str] = set()
    error_count = 0
    title: str | None = None
    for fallback_position, item in enumerate(data, start=1):
        if not isinstance(item, dict):
            error_count += 1
            continue
        url = _spotify_item_url(item)
        provider_id = _spotify_provider_id(item, url)
        if not url or not provider_id:
            error_count += 1
            continue
        if provider_id in seen:
            continue
        title = title or _text(item, "list_name")
        seen.add(provider_id)
        urls.append(url)
        entries.append(
            PlaylistEntry(
                source=Source.SPOTIFY,
                provider_id=provider_id,
                url=url,
                title=_text(item, "name") or _text(item, "title") or "Unknown title",
                artist=_spotify_artists(item),
                position=_integer(item, "list_position") or fallback_position,
            )
        )
    return PlaylistItems(
        urls=urls,
        error_count=error_count,
        entries=entries,
        title=title,
    )


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


def _youtube_provider_id(entry: object, url: str) -> str:
    """Return a stable YouTube video identifier."""
    if isinstance(entry, dict):
        video_id = _text(entry, "id")
        if video_id:
            return video_id
    query = parse_qs(urlparse(url).query)
    return query.get("v", [url])[0]


def _text(value: object, key: str) -> str | None:
    """Return a stripped string field from a JSON object."""
    if not isinstance(value, dict):
        return None
    field_value = value.get(key)
    if not isinstance(field_value, str) or not field_value.strip():
        return None
    return field_value.strip()


def _integer(value: object, key: str) -> int | None:
    """Return a positive integer field from a JSON object."""
    if not isinstance(value, dict):
        return None
    field_value = value.get(key)
    if isinstance(field_value, bool):
        return None
    if isinstance(field_value, int) and field_value > 0:
        return field_value
    if isinstance(field_value, str) and field_value.isdigit():
        parsed = int(field_value)
        return parsed if parsed > 0 else None
    return None


def _json_array_from_output(payload: str) -> list[object]:
    """Decode a JSON array even when a provider adds log lines around it."""
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        start = payload.find("[")
        end = payload.rfind("]")
        if start < 0 or end <= start:
            raise ValueError("spotDL returned invalid playlist JSON") from None
        try:
            data = json.loads(payload[start : end + 1])
        except json.JSONDecodeError as exc:
            raise ValueError("spotDL returned invalid playlist JSON") from exc
    if not isinstance(data, list):
        raise ValueError("spotDL playlist JSON was not an item list")
    return data


def _spotify_item_url(item: dict[str, object]) -> str | None:
    """Return a normalized Spotify track URL from one saved metadata item."""
    for key in ("url", "song_url"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return classify_url(value.strip()).normalized_url
    song_id = item.get("song_id") or item.get("track_id")
    if isinstance(song_id, str) and song_id.strip():
        return f"https://open.spotify.com/track/{song_id.strip()}"
    return None


def _spotify_provider_id(item: dict[str, object], url: str | None) -> str | None:
    """Return the Spotify track identifier used for canonical dedupe."""
    for key in ("song_id", "track_id"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    if not url:
        return None
    parts = [part for part in urlparse(url).path.split("/") if part]
    return parts[1] if len(parts) >= 2 and parts[0] == "track" else None


def _spotify_artists(item: dict[str, object]) -> str:
    """Normalize spotDL artist strings or arrays into one display label."""
    value = item.get("artists") or item.get("artist")
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, list):
        artists: list[str] = []
        for artist in value:
            if isinstance(artist, str) and artist.strip():
                artists.append(artist.strip())
            elif isinstance(artist, dict):
                name = _text(artist, "name")
                if name:
                    artists.append(name)
        if artists:
            return ", ".join(artists)
    return "Unknown artist"


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
