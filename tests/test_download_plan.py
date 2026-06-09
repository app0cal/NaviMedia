from pathlib import Path
from types import SimpleNamespace

from media_dl.config import Config
from media_dl.db import Job
from media_dl.download_plan import (
    OUTPUT_LAYOUT_ARTIST_ALBUM_FOLDERS,
    OUTPUT_LAYOUT_CREATOR_FOLDERS,
    OUTPUT_LAYOUT_SOURCE_FOLDERS,
    artist_album_path,
    collision_path,
    creator_path,
    plan_download,
    sanitize_path_part,
)
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


def settings(output_layout: str):
    return SimpleNamespace(
        audio_format="opus",
        thumbnail_mode="source",
        output_layout=output_layout,
        metadata_mode="source",
        playlist_mode="single_job",
    )


def job(source: Source, allow_duplicate: bool = False) -> Job:
    return Job(
        id=123,
        source=source,
        raw_url="raw",
        normalized_url="normalized",
        status="queued",
        attempts=0,
        last_error=None,
        last_warning=None,
        output_path=None,
        dedupe_key="normalized",
        duplicate_of=None,
        allow_duplicate=allow_duplicate,
    )


def test_source_folder_plan_keeps_legacy_roots(tmp_path):
    youtube = plan_download(config(tmp_path), job(Source.YOUTUBE), settings(OUTPUT_LAYOUT_SOURCE_FOLDERS))
    spotify = plan_download(config(tmp_path), job(Source.SPOTIFY), settings(OUTPUT_LAYOUT_SOURCE_FOLDERS))

    assert youtube.output_layout.output_root == tmp_path / "music" / "YouTube"
    assert spotify.output_layout.output_root == tmp_path / "music" / "Spotify"
    assert youtube.output_layout.effective_layout == OUTPUT_LAYOUT_SOURCE_FOLDERS
    assert spotify.output_layout.effective_layout == OUTPUT_LAYOUT_SOURCE_FOLDERS


def test_creator_folder_plan_uses_music_root_with_creator_templates(tmp_path):
    youtube = plan_download(config(tmp_path), job(Source.YOUTUBE), settings(OUTPUT_LAYOUT_CREATOR_FOLDERS))
    spotify = plan_download(config(tmp_path), job(Source.SPOTIFY), settings(OUTPUT_LAYOUT_CREATOR_FOLDERS))

    assert youtube.output_layout.output_root == tmp_path / "music"
    assert "%(uploader,channel|Unknown Artist)s" in youtube.output_layout.output_template
    assert spotify.output_layout.output_template.endswith("{artist}/{title}.{output-ext}")


def test_artist_album_plan_prefers_spotify_album_layout_and_youtube_creator_fallback(tmp_path):
    youtube = plan_download(
        config(tmp_path),
        job(Source.YOUTUBE),
        settings(OUTPUT_LAYOUT_ARTIST_ALBUM_FOLDERS),
    )
    spotify = plan_download(
        config(tmp_path),
        job(Source.SPOTIFY),
        settings(OUTPUT_LAYOUT_ARTIST_ALBUM_FOLDERS),
    )

    assert youtube.output_layout.name == OUTPUT_LAYOUT_ARTIST_ALBUM_FOLDERS
    assert youtube.output_layout.effective_layout == OUTPUT_LAYOUT_CREATOR_FOLDERS
    assert spotify.output_layout.effective_layout == OUTPUT_LAYOUT_ARTIST_ALBUM_FOLDERS
    assert spotify.output_layout.output_template.endswith(
        "{artist}/{album}/{track-number} - {title}.{output-ext}"
    )


def test_duplicate_plan_uses_job_id_source_root_and_disables_youtube_archive(tmp_path):
    planned = plan_download(
        config(tmp_path),
        job(Source.YOUTUBE, allow_duplicate=True),
        settings(OUTPUT_LAYOUT_ARTIST_ALBUM_FOLDERS),
    )

    assert planned.output_layout.output_root == tmp_path / "music" / "Duplicates" / "123" / "YouTube"
    assert planned.duplicate_job_id == 123
    assert planned.use_archive is False


def test_artist_album_path_falls_back_to_artist_root_without_album(tmp_path):
    path = artist_album_path(
        tmp_path / "music",
        artist="Jawed",
        album=None,
        title="Song",
        extension="opus",
        track_number=7,
    )

    assert path == tmp_path / "music" / "Jawed" / "Song.opus"


def test_creator_path_uses_unknown_artist_when_creator_missing(tmp_path):
    path = creator_path(tmp_path / "music", creator="", title="Song", extension=".opus")

    assert path == tmp_path / "music" / "Unknown Artist" / "Song.opus"


def test_path_sanitization_and_collision_suffix(tmp_path):
    unsafe = " Bad/Name\tWith\\Separators "
    original = tmp_path / "music" / "Artist" / "Song.opus"

    assert sanitize_path_part(unsafe) == "Bad Name With Separators"
    assert collision_path(original, 123) == tmp_path / "music" / "Artist" / "Song [job-123].opus"
