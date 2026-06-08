from pathlib import Path

from media_dl.config import Config
from media_dl.db import Job
from media_dl.downloader import Downloader
from media_dl.urltools import Source


def config(tmp_path: Path) -> Config:
    return Config(
        music_root=tmp_path / "music",
        state_dir=tmp_path / "state",
        queue_dir=tmp_path / "queue",
        download_dir=tmp_path / "downloads",
        yt_dlp_bin="yt-dlp",
        spotdl_bin="spotdl",
    )


def test_youtube_command_contains_playlist_archive_and_metadata(tmp_path):
    downloader = Downloader(config(tmp_path))
    command = downloader.youtube_command("https://youtube.com/playlist?list=PL123")

    assert command[0] == "yt-dlp"
    assert "--yes-playlist" in command
    assert "--download-archive" in command
    assert "--embed-metadata" in command
    assert "--embed-thumbnail" in command
    assert "https://youtube.com/playlist?list=PL123" == command[-1]


def test_spotify_command_uses_spotdl_m4a_without_transcoding(tmp_path):
    downloader = Downloader(config(tmp_path))
    command = downloader.spotify_command("https://open.spotify.com/playlist/abc")

    assert command[:2] == ["spotdl", "download"]
    assert "--format" in command
    assert "m4a" in command
    assert "--bitrate" in command
    assert "disable" in command


def test_command_for_rejects_unknown_source(tmp_path):
    downloader = Downloader(config(tmp_path))
    job = Job(
        id=1,
        source=Source.UNKNOWN,
        raw_url="raw",
        normalized_url="normalized",
        status="queued",
        attempts=0,
        last_error=None,
        output_path=None,
    )

    try:
        downloader.command_for(job)
    except ValueError as exc:
        assert "unsupported source" in str(exc)
    else:
        raise AssertionError("expected unsupported source error")
