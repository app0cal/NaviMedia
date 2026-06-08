from media_dl.urltools import Source, classify_url


def test_classifies_youtube_video_and_strips_tracking():
    result = classify_url("https://www.youtube.com/watch?v=abc123&utm_source=x&feature=share")

    assert result.source == Source.YOUTUBE
    assert result.normalized_url == "https://www.youtube.com/watch?v=abc123"


def test_classifies_youtube_music_playlist():
    result = classify_url("https://music.youtube.com/playlist?list=PL123&si=tracking")

    assert result.source == Source.YOUTUBE
    assert result.normalized_url == "https://music.youtube.com/playlist?list=PL123"


def test_classifies_spotify_playlist():
    result = classify_url("https://open.spotify.com/playlist/abc123?si=tracking")

    assert result.source == Source.SPOTIFY
    assert result.normalized_url == "https://open.spotify.com/playlist/abc123"


def test_unknown_source():
    result = classify_url("https://example.com/item")

    assert result.source == Source.UNKNOWN
