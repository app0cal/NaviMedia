"""Tests for YouTube playlist detection and flat-extraction parsing."""

import json

import pytest

from media_dl.playlist import (
    is_youtube_playlist_url,
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


def test_parse_youtube_playlist_json_rejects_invalid_payload():
    """Verify invalid or non-playlist JSON payloads fail clearly."""
    with pytest.raises(ValueError, match="invalid playlist JSON"):
        parse_youtube_playlist_json("not json")

    with pytest.raises(ValueError, match="did not include entries"):
        parse_youtube_playlist_json("{}")
