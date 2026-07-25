"""Tests for YouTube playlist detection and flat-extraction parsing."""

import json

import pytest

from media_dl.playlist import (
    is_playlist_url,
    is_spotify_playlist_url,
    is_youtube_playlist_url,
    parse_spotify_playlist_json,
    parse_youtube_playlist_json,
)


def test_detects_youtube_playlist_urls():
    """Verify playlist detection includes YouTube Music and watch URLs with list ids."""
    assert is_youtube_playlist_url("https://www.youtube.com/playlist?list=PL123")
    assert is_youtube_playlist_url("https://music.youtube.com/playlist?list=PL123")
    assert is_youtube_playlist_url("https://www.youtube.com/watch?v=abc&list=PL123")
    assert not is_youtube_playlist_url("https://www.youtube.com/watch?v=abc")
    assert not is_youtube_playlist_url("https://open.spotify.com/playlist/abc")


def test_parse_youtube_playlist_json_normalizes_video_urls():
    """Verify parser returns normalized child video URLs and counts bad entries."""
    payload = json.dumps(
        {
            "entries": [
                {"id": "abc"},
                {"url": "def"},
                {"webpage_url": "https://www.youtube.com/watch?v=ghi&list=PL123"},
                {"webpage_url": "https://example.com/nope"},
                {},
            ]
        }
    )

    items = parse_youtube_playlist_json(payload)

    assert items.urls == [
        "https://www.youtube.com/watch?v=abc",
        "https://www.youtube.com/watch?v=def",
        "https://www.youtube.com/watch?v=ghi",
    ]
    assert items.error_count == 2
    assert items.entries[0].title == "Unknown title"
    assert items.entries[0].provider_id == "abc"


def test_parse_youtube_playlist_json_rejects_invalid_payload():
    """Verify invalid or non-playlist JSON payloads fail clearly."""
    with pytest.raises(ValueError, match="invalid playlist JSON"):
        parse_youtube_playlist_json("not json")

    with pytest.raises(ValueError, match="did not include entries"):
        parse_youtube_playlist_json("{}")


def test_parse_youtube_playlist_metadata():
    """Verify title and artist metadata are retained for the dashboard."""
    items = parse_youtube_playlist_json(
        json.dumps(
            {
                "title": "Mix",
                "entries": [
                    {
                        "id": "abc",
                        "title": "Track",
                        "artist": "Artist",
                        "uploader": "Fallback",
                    }
                ],
            }
        )
    )

    assert items.title == "Mix"
    assert items.entries[0].title == "Track"
    assert items.entries[0].artist == "Artist"


def test_spotify_playlist_detection_and_metadata_parsing():
    """Verify Spotify playlist URLs and spotDL save JSON normalize correctly."""
    url = "https://open.spotify.com/playlist/list123"
    assert is_spotify_playlist_url(url)
    assert is_playlist_url(url)
    assert not is_spotify_playlist_url("https://open.spotify.com/track/track123")

    items = parse_spotify_playlist_json(
        "log line\n"
        + json.dumps(
            [
                {
                    "song_id": "track123",
                    "url": "https://open.spotify.com/track/track123",
                    "name": "Song",
                    "artists": ["First", "Second"],
                }
            ]
        )
    )

    assert items.urls == ["https://open.spotify.com/track/track123"]
    assert items.entries[0].provider_id == "track123"
    assert items.entries[0].artist == "First, Second"
