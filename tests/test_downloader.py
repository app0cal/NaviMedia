from pathlib import Path
from dataclasses import replace
from types import SimpleNamespace

from media_dl.config import Config
from media_dl.db import Job
from media_dl.downloader import Downloader, _warning_from_output
from media_dl.download_plan import MetadataPolicy
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


def test_youtube_command_omits_thumbnail_for_none_or_default_mode(tmp_path):
    none_cfg = config(tmp_path)
    default_cfg = Config(
        music_root=tmp_path / "music",
        state_dir=tmp_path / "state",
        queue_dir=tmp_path / "queue",
        download_dir=tmp_path / "downloads",
        thumbnail_mode="default",
    )

    none_command = Downloader(replace(none_cfg, thumbnail_mode="none")).youtube_command(
        "https://youtube.com/watch?v=abc"
    )
    default_command = Downloader(default_cfg).youtube_command("https://youtube.com/watch?v=abc")

    assert "--embed-thumbnail" not in none_command
    assert "--embed-thumbnail" not in default_command


def test_duplicate_youtube_command_omits_archive_and_uses_duplicate_path(tmp_path):
    downloader = Downloader(config(tmp_path))
    command = downloader.youtube_command("https://youtube.com/watch?v=abc", duplicate_job_id=42)

    assert "--download-archive" not in command
    duplicate_root = str(tmp_path / "music" / "Duplicates" / "42" / "YouTube")
    assert any(duplicate_root in arg for arg in command)


def test_spotify_command_uses_spotdl_m4a_without_transcoding(tmp_path):
    downloader = Downloader(config(tmp_path))
    command = downloader.spotify_command("https://open.spotify.com/playlist/abc")

    assert command[:2] == ["spotdl", "download"]
    assert "--format" in command
    assert "m4a" in command
    assert "--bitrate" in command
    assert "disable" in command
    assert "--skip-album-art" not in command


def test_spotify_command_skips_album_art_for_none_or_default_mode(tmp_path):
    cfg = Config(
        music_root=tmp_path / "music",
        state_dir=tmp_path / "state",
        queue_dir=tmp_path / "queue",
        download_dir=tmp_path / "downloads",
        thumbnail_mode="none",
    )
    command = Downloader(cfg).spotify_command("https://open.spotify.com/playlist/abc")

    assert "--skip-album-art" in command


def test_duplicate_spotify_command_uses_duplicate_path(tmp_path):
    downloader = Downloader(config(tmp_path))
    command = downloader.spotify_command("https://open.spotify.com/playlist/abc", duplicate_job_id=42)

    duplicate_root = str(tmp_path / "music" / "Duplicates" / "42" / "Spotify")
    assert any(duplicate_root in arg for arg in command)


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
        last_warning=None,
        output_path=None,
        dedupe_key="normalized",
        duplicate_of=None,
        allow_duplicate=False,
    )

    try:
        downloader.command_for(job)
    except ValueError as exc:
        assert "unsupported source" in str(exc)
    else:
        raise AssertionError("expected unsupported source error")


def test_warning_from_successful_playlist_output_with_error():
    warning = _warning_from_output(
        "[download] Downloading item 11 of 11\n"
        "ERROR: [youtube] abc: Video unavailable. This video is not available\n"
        "[download] Finished downloading playlist: playlist\n"
    )

    assert warning == (
        "Some playlist items failed. First error: "
        "ERROR: [youtube] abc: Video unavailable. This video is not available"
    )


def test_warning_from_output_ignores_normal_thumbnail_probe_messages():
    warning = _warning_from_output(
        "[info] Downloading video thumbnail 45 ...\n"
        "[info] Video Thumbnail 45 does not exist\n"
        "[info] Writing video thumbnail 41 to: output.webp\n"
    )

    assert warning is None


def test_run_treats_finished_playlist_item_error_as_warning(tmp_path, monkeypatch):
    downloader = Downloader(config(tmp_path))
    job = Job(
        id=1,
        source=Source.YOUTUBE,
        raw_url="raw",
        normalized_url="https://youtube.com/playlist?list=PL123",
        status="queued",
        attempts=0,
        last_error=None,
        last_warning=None,
        output_path=None,
        dedupe_key="normalized",
        duplicate_of=None,
        allow_duplicate=False,
    )

    def fake_run(*args, **kwargs):
        return SimpleNamespace(
            returncode=1,
            stdout=(
                "ERROR: [youtube] abc: Video unavailable. This video is not available\n"
                "[download] Finished downloading playlist: playlist\n"
            ),
        )

    monkeypatch.setattr("subprocess.run", fake_run)

    result = downloader.run(job)

    assert result.output_path == tmp_path / "music" / "YouTube"
    assert result.warning.startswith("Some playlist items failed.")


def test_run_still_fails_nonzero_without_finished_playlist(tmp_path, monkeypatch):
    downloader = Downloader(config(tmp_path))
    job = Job(
        id=1,
        source=Source.YOUTUBE,
        raw_url="raw",
        normalized_url="https://youtube.com/watch?v=abc",
        status="queued",
        attempts=0,
        last_error=None,
        last_warning=None,
        output_path=None,
        dedupe_key="normalized",
        duplicate_of=None,
        allow_duplicate=False,
    )

    def fake_run(*args, **kwargs):
        return SimpleNamespace(returncode=1, stdout="ERROR: hard failure\n")

    monkeypatch.setattr("subprocess.run", fake_run)

    try:
        downloader.run(job)
    except RuntimeError as exc:
        assert "hard failure" in str(exc)
    else:
        raise AssertionError("expected non-playlist failure")


def test_run_applies_default_thumbnail_to_new_audio_file(tmp_path, monkeypatch):
    cfg = Config(
        music_root=tmp_path / "music",
        state_dir=tmp_path / "state",
        queue_dir=tmp_path / "queue",
        download_dir=tmp_path / "downloads",
        config_dir=tmp_path / "config",
        default_thumbnail_path=tmp_path / "config" / "default.jpg",
        thumbnail_mode="default",
    )
    cfg.ensure_dirs()
    cfg.resolved_default_thumbnail_path.write_bytes(b"fake-jpeg")
    output_dir = cfg.music_root / "YouTube"
    output_dir.mkdir(parents=True, exist_ok=True)
    audio_path = output_dir / "song.mp3"

    job = Job(
        id=1,
        source=Source.YOUTUBE,
        raw_url="raw",
        normalized_url="https://youtube.com/watch?v=abc",
        status="queued",
        attempts=0,
        last_error=None,
        last_warning=None,
        output_path=None,
        dedupe_key="normalized",
        duplicate_of=None,
        allow_duplicate=False,
    )
    embedded = {}

    def fake_run(*args, **kwargs):
        audio_path.write_bytes(b"audio")
        return SimpleNamespace(returncode=0, stdout="")

    def fake_embed(path, image_bytes, mime):
        embedded["path"] = path
        embedded["image_bytes"] = image_bytes
        embedded["mime"] = mime

    monkeypatch.setattr("subprocess.run", fake_run)
    monkeypatch.setattr("media_dl.downloader._embed_thumbnail", fake_embed)

    result = Downloader(cfg).run(job)

    assert result.warning is None
    assert embedded == {
        "path": audio_path,
        "image_bytes": b"fake-jpeg",
        "mime": "image/jpeg",
    }


def test_default_thumbnail_missing_returns_warning(tmp_path, monkeypatch):
    cfg = Config(
        music_root=tmp_path / "music",
        state_dir=tmp_path / "state",
        queue_dir=tmp_path / "queue",
        download_dir=tmp_path / "downloads",
        config_dir=tmp_path / "config",
        default_thumbnail_path=tmp_path / "config" / "default.jpg",
        thumbnail_mode="default",
    )
    cfg.ensure_dirs()
    job = Job(
        id=1,
        source=Source.YOUTUBE,
        raw_url="raw",
        normalized_url="https://youtube.com/watch?v=abc",
        status="queued",
        attempts=0,
        last_error=None,
        last_warning=None,
        output_path=None,
        dedupe_key="normalized",
        duplicate_of=None,
        allow_duplicate=False,
    )

    def fake_run(*args, **kwargs):
        return SimpleNamespace(returncode=0, stdout="")

    monkeypatch.setattr("subprocess.run", fake_run)

    result = Downloader(cfg).run(job)

    assert result.warning == f"default thumbnail not found: {cfg.resolved_default_thumbnail_path}"


def test_run_applies_navidrome_metadata_cleanup_to_new_audio_file(tmp_path, monkeypatch):
    cfg = config(tmp_path)
    cfg.ensure_dirs()
    output_dir = cfg.music_root / "YouTube"
    output_dir.mkdir(parents=True, exist_ok=True)
    audio_path = output_dir / "song.opus"
    settings = SimpleNamespace(
        audio_format="opus",
        thumbnail_mode="source",
        output_layout="source_folders",
        metadata_mode="navidrome_clean",
        playlist_mode="single_job",
    )
    job = Job(
        id=1,
        source=Source.YOUTUBE,
        raw_url="raw",
        normalized_url="https://youtube.com/watch?v=abc",
        status="queued",
        attempts=0,
        last_error=None,
        last_warning=None,
        output_path=None,
        dedupe_key="normalized",
        duplicate_of=None,
        allow_duplicate=False,
    )
    cleaned = {}

    def fake_run(*args, **kwargs):
        audio_path.write_bytes(b"audio")
        return SimpleNamespace(returncode=0, stdout="")

    def fake_clean(paths, policy):
        cleaned["paths"] = paths
        cleaned["policy"] = policy
        return None

    monkeypatch.setattr("subprocess.run", fake_run)
    monkeypatch.setattr("media_dl.downloader.clean_new_audio_metadata", fake_clean)

    result = Downloader(cfg, settings=settings).run(job)

    assert result.warning is None
    assert cleaned == {
        "paths": [audio_path],
        "policy": MetadataPolicy("navidrome_clean"),
    }


def test_run_metadata_cleanup_warning_completes_job_with_warning(tmp_path, monkeypatch):
    cfg = config(tmp_path)
    cfg.ensure_dirs()
    output_dir = cfg.music_root / "YouTube"
    output_dir.mkdir(parents=True, exist_ok=True)
    audio_path = output_dir / "song.opus"
    settings = SimpleNamespace(
        audio_format="opus",
        thumbnail_mode="source",
        output_layout="source_folders",
        metadata_mode="navidrome_clean",
        playlist_mode="single_job",
    )
    job = Job(
        id=1,
        source=Source.YOUTUBE,
        raw_url="raw",
        normalized_url="https://youtube.com/watch?v=abc",
        status="queued",
        attempts=0,
        last_error=None,
        last_warning=None,
        output_path=None,
        dedupe_key="normalized",
        duplicate_of=None,
        allow_duplicate=False,
    )

    def fake_run(*args, **kwargs):
        audio_path.write_bytes(b"audio")
        return SimpleNamespace(returncode=0, stdout="")

    monkeypatch.setattr("subprocess.run", fake_run)
    monkeypatch.setattr(
        "media_dl.downloader.clean_new_audio_metadata",
        lambda paths, policy: "metadata cleanup failed for 1 file(s): song.opus: bad tags",
    )

    result = Downloader(cfg, settings=settings).run(job)

    assert result.output_path == output_dir
    assert result.warning == "metadata cleanup failed for 1 file(s): song.opus: bad tags"
