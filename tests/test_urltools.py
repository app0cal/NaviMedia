"""Tests for URL classification and normalization."""

from media_dl.urltools import Source, classify_url


def test_classifies_youtube_video_and_strips_tracking():
    """Verify YouTube video URLs are classified and tracking parameters removed."""
    result = classify_url("https://www.youtube.com/watch?v=abc123&utm_source=x&feature=share")

    assert result.source == Source.YOUTUBE
    assert result.normalized_url == "https://www.youtube.com/watch?v=abc123"


def test_classifies_youtube_music_playlist():
    """Verify YouTube Music playlists classify as YouTube."""
    result = classify_url("https://music.youtube.com/playlist?list=PL123&si=tracking")

    assert result.source == Source.YOUTUBE
    assert result.normalized_url == "https://music.youtube.com/playlist?list=PL123"


def test_classifies_spotify_playlist():
    """Verify Spotify playlist URLs classify as Spotify."""
    result = classify_url("https://open.spotify.com/playlist/abc123?si=tracking")

    assert result.source == Source.SPOTIFY
    assert result.normalized_url == "https://open.spotify.com/playlist/abc123"


def test_unknown_source():
    """Verify unsupported hosts classify as unknown."""
    result = classify_url("https://example.com/item")

    assert result.source == Source.UNKNOWN
