from pathlib import Path

from media_dl.download_plan import MetadataPolicy
from media_dl.metadata import clean_audio_metadata, clean_new_audio_metadata


class FakeAudio(dict):
    def __init__(self, values=None):
        super().__init__(values or {})
        self.saved = False

    def save(self):
        self.saved = True


def test_clean_audio_metadata_fills_missing_navidirome_core_tags(tmp_path, monkeypatch):
    path = tmp_path / "Song Name.mp3"
    audio = FakeAudio()
    monkeypatch.setattr("media_dl.metadata._open_audio", lambda path: audio)

    clean_audio_metadata(path, MetadataPolicy("navidrome_clean"))

    assert audio["title"] == ["Song Name"]
    assert audio["artist"] == ["Unknown Artist"]
    assert audio["albumartist"] == ["Unknown Artist"]
    assert "album" not in audio
    assert audio.saved is True


def test_clean_audio_metadata_preserves_existing_tags_and_multi_artist(tmp_path, monkeypatch):
    path = tmp_path / "ignored.mp3"
    audio = FakeAudio(
        {
            "title": ["Existing Title"],
            "artist": ["Artist One", "Artist Two"],
            "albumartist": ["Existing Album Artist"],
            "album": ["Existing Album"],
            "tracknumber": ["3"],
        }
    )
    monkeypatch.setattr("media_dl.metadata._open_audio", lambda path: audio)

    clean_audio_metadata(path, MetadataPolicy("navidrome_clean"))

    assert audio["title"] == ["Existing Title"]
    assert audio["artist"] == ["Artist One", "Artist Two"]
    assert audio["albumartist"] == ["Existing Album Artist"]
    assert audio["album"] == ["Existing Album"]
    assert audio["tracknumber"] == ["3"]
    assert audio.saved is False


def test_clean_audio_metadata_copies_artist_to_missing_album_artist(tmp_path, monkeypatch):
    path = tmp_path / "song.flac"
    audio = FakeAudio({"title": ["Song"], "artist": ["Artist One", "Artist Two"]})
    monkeypatch.setattr("media_dl.metadata._open_audio", lambda path: audio)

    clean_audio_metadata(path, MetadataPolicy("navidrome_clean"))

    assert audio["albumartist"] == ["Artist One", "Artist Two"]
    assert audio.saved is True


def test_clean_new_audio_metadata_is_noop_for_source_mode(tmp_path, monkeypatch):
    called = False

    def fake_open(path):
        nonlocal called
        called = True
        return FakeAudio()

    monkeypatch.setattr("media_dl.metadata._open_audio", fake_open)

    warning = clean_new_audio_metadata([tmp_path / "song.mp3"], MetadataPolicy("source"))

    assert warning is None
    assert called is False


def test_clean_new_audio_metadata_warns_for_wav(tmp_path):
    warning = clean_new_audio_metadata([tmp_path / "song.wav"], MetadataPolicy("navidrome_clean"))

    assert warning == "metadata cleanup failed for 1 file(s): song.wav: wav metadata cleanup is not supported"
