"""Tests for worker job processing, settings usage, and playlist expansion."""

from media_dl.config import Config
from media_dl.db import Database
from media_dl.downloader import DownloadResult
from media_dl.playlist import PlaylistEntry, PlaylistItems
from media_dl.urltools import Source
from media_dl.worker import process_job, run_once
from media_dl.runtime_settings import save_runtime_settings


def test_process_job_ignores_completed_job(tmp_path):
    """Verify completed jobs are not processed again."""
    cfg = Config(
        music_root=tmp_path / "music",
        state_dir=tmp_path / "state",
        queue_dir=tmp_path / "queue",
        download_dir=tmp_path / "downloads",
    )
    db = Database(cfg.db_path)
    job, _ = db.add_job(Source.YOUTUBE, "raw", "normalized")
    db.mark_complete(job.id, "output")
    db.close()

    processed = process_job(cfg, job.id)

    assert processed.status == "complete"
    assert processed.attempts == 0


def test_process_job_uses_persisted_audio_format(tmp_path, monkeypatch):
    """Verify worker applies persisted audio format to downloader config."""
    cfg = Config(
        music_root=tmp_path / "music",
        state_dir=tmp_path / "state",
        queue_dir=tmp_path / "queue",
        download_dir=tmp_path / "downloads",
        yt_dlp_bin="yt-dlp",
        spotdl_bin="spotdl",
    )
    save_runtime_settings(cfg, "opus")
    db = Database(cfg.db_path)
    job, _ = db.add_job(Source.YOUTUBE, "raw", "normalized")
    db.close()

    seen = {}

    def fake_run(self, job):
        """Capture the audio format passed to the downloader."""
        seen["audio_format"] = self.config.audio_format
        return DownloadResult(output_path=cfg.music_root / "YouTube")

    monkeypatch.setattr("media_dl.downloader.Downloader.run", fake_run)

    processed = process_job(cfg, job.id)

    assert seen["audio_format"] == "opus"
    assert processed.status == "complete"


def test_process_job_uses_persisted_thumbnail_mode(tmp_path, monkeypatch):
    """Verify worker applies persisted thumbnail mode to downloader config."""
    cfg = Config(
        music_root=tmp_path / "music",
        state_dir=tmp_path / "state",
        queue_dir=tmp_path / "queue",
        download_dir=tmp_path / "downloads",
    )
    save_runtime_settings(cfg, "m4a", thumbnail_mode="none")
    db = Database(cfg.db_path)
    job, _ = db.add_job(Source.YOUTUBE, "raw", "normalized")
    db.close()

    seen = {}

    def fake_run(self, job):
        """Capture the thumbnail mode passed to the downloader."""
        seen["thumbnail_mode"] = self.config.thumbnail_mode
        return DownloadResult(output_path=cfg.music_root / "YouTube")

    monkeypatch.setattr("media_dl.downloader.Downloader.run", fake_run)

    processed = process_job(cfg, job.id)

    assert seen["thumbnail_mode"] == "none"
    assert processed.status == "complete"


def test_process_job_uses_persisted_output_layout(tmp_path, monkeypatch):
    """Verify worker passes persisted output layout through runtime settings."""
    cfg = Config(
        music_root=tmp_path / "music",
        state_dir=tmp_path / "state",
        queue_dir=tmp_path / "queue",
        download_dir=tmp_path / "downloads",
    )
    save_runtime_settings(cfg, "m4a", output_layout="creator_folders")
    db = Database(cfg.db_path)
    job, _ = db.add_job(Source.YOUTUBE, "raw", "normalized")
    db.close()

    seen = {}

    def fake_run(self, job):
        """Capture the output layout passed to the downloader."""
        seen["output_layout"] = self.settings.output_layout
        return DownloadResult(output_path=cfg.music_root)

    monkeypatch.setattr("media_dl.downloader.Downloader.run", fake_run)

    processed = process_job(cfg, job.id)

    assert seen["output_layout"] == "creator_folders"
    assert processed.status == "complete"


def test_process_job_persists_success_warning(tmp_path, monkeypatch):
    """Verify non-fatal downloader warnings persist on completed jobs."""
    cfg = Config(
        music_root=tmp_path / "music",
        state_dir=tmp_path / "state",
        queue_dir=tmp_path / "queue",
        download_dir=tmp_path / "downloads",
    )
    db = Database(cfg.db_path)
    job, _ = db.add_job(Source.YOUTUBE, "raw", "normalized")
    db.close()

    def fake_run(self, job):
        """Return a successful download result with a warning."""
        return DownloadResult(
            output_path=cfg.music_root / "YouTube",
            warning="Some playlist items failed.",
        )

    monkeypatch.setattr("media_dl.downloader.Downloader.run", fake_run)

    processed = process_job(cfg, job.id)

    assert processed.status == "complete"
    assert processed.last_warning == "Some playlist items failed."
    assert processed.last_error is None


def test_process_job_expands_youtube_playlist(tmp_path, monkeypatch):
    """Verify YouTube playlist expansion completes parent and queues children."""
    cfg = Config(
        music_root=tmp_path / "music",
        state_dir=tmp_path / "state",
        queue_dir=tmp_path / "queue",
        download_dir=tmp_path / "downloads",
    )
    save_runtime_settings(cfg, "m4a", playlist_mode="expand_items")
    db = Database(cfg.db_path)
    parent, _ = db.add_job(
        Source.YOUTUBE,
        "https://www.youtube.com/playlist?list=PL123",
        "https://www.youtube.com/playlist?list=PL123",
    )
    db.close()

    monkeypatch.setattr(
        "media_dl.worker.extract_youtube_playlist_items",
        lambda _bin, _url: PlaylistItems(
            urls=[
                "https://www.youtube.com/watch?v=abc",
                "https://www.youtube.com/watch?v=def",
            ],
            error_count=1,
        ),
    )

    processed = process_job(cfg, parent.id)

    db = Database(cfg.db_path)
    jobs = db.list_jobs(limit=10)
    db.close()

    assert processed.status == "complete"
    assert processed.child_count == 3
    assert processed.child_created_count == 2
    assert processed.child_duplicate_count == 0
    assert processed.child_error_count == 1
    assert processed.last_warning == "Expanded playlist: 2 queued, 0 duplicates, 1 errors."
    children = [job for job in jobs if job.parent_id == parent.id]
    assert [job.normalized_url for job in children] == [
        "https://www.youtube.com/watch?v=def",
        "https://www.youtube.com/watch?v=abc",
    ]


def test_process_job_counts_duplicate_playlist_children(tmp_path, monkeypatch):
    """Verify duplicate extracted children are counted but not attached."""
    cfg = Config(
        music_root=tmp_path / "music",
        state_dir=tmp_path / "state",
        queue_dir=tmp_path / "queue",
        download_dir=tmp_path / "downloads",
    )
    save_runtime_settings(cfg, "m4a", playlist_mode="expand_items")
    db = Database(cfg.db_path)
    db.add_job(
        Source.YOUTUBE,
        "https://www.youtube.com/watch?v=abc",
        "https://www.youtube.com/watch?v=abc",
    )
    parent, _ = db.add_job(
        Source.YOUTUBE,
        "https://www.youtube.com/playlist?list=PL123",
        "https://www.youtube.com/playlist?list=PL123",
    )
    db.close()

    monkeypatch.setattr(
        "media_dl.worker.extract_youtube_playlist_items",
        lambda _bin, _url: PlaylistItems(
            urls=[
                "https://www.youtube.com/watch?v=abc",
                "https://www.youtube.com/watch?v=def",
            ],
            error_count=0,
        ),
    )

    processed = process_job(cfg, parent.id)

    db = Database(cfg.db_path)
    children = [job for job in db.list_jobs(limit=10) if job.parent_id == parent.id]
    db.close()

    assert processed.child_count == 2
    assert processed.child_created_count == 1
    assert processed.child_duplicate_count == 1
    assert [job.normalized_url for job in children] == ["https://www.youtube.com/watch?v=def"]


def test_process_job_marks_playlist_extraction_failure_failed(tmp_path, monkeypatch):
    """Verify extraction failures fail the parent before child creation."""
    cfg = Config(
        music_root=tmp_path / "music",
        state_dir=tmp_path / "state",
        queue_dir=tmp_path / "queue",
        download_dir=tmp_path / "downloads",
    )
    save_runtime_settings(cfg, "m4a", playlist_mode="expand_items")
    db = Database(cfg.db_path)
    parent, _ = db.add_job(
        Source.YOUTUBE,
        "https://www.youtube.com/playlist?list=PL123",
        "https://www.youtube.com/playlist?list=PL123",
    )
    db.close()

    def fail(_bin, _url):
        """Raise the extraction failure used by the worker."""
        raise RuntimeError("extract failed")

    monkeypatch.setattr("media_dl.worker.extract_youtube_playlist_items", fail)

    processed = process_job(cfg, parent.id)

    assert processed.status == "failed"
    assert processed.last_error == "extract failed"
    assert processed.child_created_count == 0


def test_process_job_does_not_expand_youtube_video(tmp_path, monkeypatch):
    """Verify YouTube videos still download normally in expand mode."""
    cfg = Config(
        music_root=tmp_path / "music",
        state_dir=tmp_path / "state",
        queue_dir=tmp_path / "queue",
        download_dir=tmp_path / "downloads",
    )
    save_runtime_settings(cfg, "m4a", playlist_mode="expand_items")
    db = Database(cfg.db_path)
    job, _ = db.add_job(
        Source.YOUTUBE,
        "https://www.youtube.com/watch?v=abc",
        "https://www.youtube.com/watch?v=abc",
    )
    db.close()

    def fake_run(self, job):
        """Return a successful YouTube video download result."""
        return DownloadResult(output_path=cfg.music_root / "YouTube")

    monkeypatch.setattr("media_dl.downloader.Downloader.run", fake_run)

    processed = process_job(cfg, job.id)

    assert processed.status == "complete"
    assert processed.child_count == 0


def test_spotify_playlist_expand_mode_falls_back_to_download(tmp_path, monkeypatch):
    """Verify Spotify playlists keep single-job behavior in expand mode."""
    cfg = Config(
        music_root=tmp_path / "music",
        state_dir=tmp_path / "state",
        queue_dir=tmp_path / "queue",
        download_dir=tmp_path / "downloads",
    )
    save_runtime_settings(cfg, "m4a", playlist_mode="expand_items")
    db = Database(cfg.db_path)
    job, _ = db.add_job(
        Source.SPOTIFY,
        "https://open.spotify.com/playlist/abc",
        "https://open.spotify.com/playlist/abc",
    )
    db.close()

    def fake_run(self, job):
        """Return a successful Spotify download result."""
        return DownloadResult(output_path=cfg.music_root / "Spotify")

    monkeypatch.setattr("media_dl.downloader.Downloader.run", fake_run)

    processed = process_job(cfg, job.id)

    assert processed.status == "complete"
    assert processed.child_count == 0


def test_run_once_counts_parent_expansion_as_one_job(tmp_path, monkeypatch):
    """Verify manual run counts playlist parent expansion as one processed job."""
    cfg = Config(
        music_root=tmp_path / "music",
        state_dir=tmp_path / "state",
        queue_dir=tmp_path / "queue",
        download_dir=tmp_path / "downloads",
    )
    save_runtime_settings(cfg, "m4a", playlist_mode="expand_items")
    db = Database(cfg.db_path)
    db.add_job(
        Source.YOUTUBE,
        "https://www.youtube.com/playlist?list=PL123",
        "https://www.youtube.com/playlist?list=PL123",
    )
    db.close()

    monkeypatch.setattr(
        "media_dl.worker.extract_youtube_playlist_items",
        lambda _bin, _url: PlaylistItems(
            urls=["https://www.youtube.com/watch?v=abc"],
            error_count=0,
        ),
    )

    processed_count = run_once(cfg, max_jobs=1)

    db = Database(cfg.db_path)
    queued = db.next_queued()
    db.close()

    assert processed_count == 1
    assert queued is not None
    assert queued.normalized_url == "https://www.youtube.com/watch?v=abc"


def test_tracked_playlist_queues_canonical_item_downloads(tmp_path, monkeypatch):
    """Verify tracked playlists itemize current songs and persist child work."""
    cfg = Config(
        music_root=tmp_path / "music",
        state_dir=tmp_path / "state",
        queue_dir=tmp_path / "queue",
        download_dir=tmp_path / "downloads",
    )
    db = Database(cfg.db_path)
    job, _ = db.add_job(
        Source.YOUTUBE,
        "https://youtube.com/playlist?list=PL123",
        "https://youtube.com/playlist?list=PL123",
    )
    playlist = db.ensure_playlist(job, tracked=True, interval_seconds=86400)
    db.close()
    monkeypatch.setattr(
        "media_dl.worker.extract_youtube_playlist_items",
        lambda _bin, _url: PlaylistItems(
            urls=["https://www.youtube.com/watch?v=abc"],
            error_count=0,
            title="Mix",
            entries=[
                PlaylistEntry(
                    source=Source.YOUTUBE,
                    provider_id="abc",
                    url="https://www.youtube.com/watch?v=abc",
                    title="Song",
                    artist="Artist",
                    position=1,
                )
            ],
        ),
    )

    processed = process_job(cfg, job.id)

    db = Database(cfg.db_path)
    children = [
        child for child in db.list_jobs(limit=10) if child.parent_id == job.id
    ]
    items, total = db.list_playlist_items(playlist.id)
    pending_work = db.conn.execute(
        "SELECT COUNT(*) AS total FROM work_items WHERE status = 'pending'"
    ).fetchone()["total"]
    scheduled = db.get_playlist(playlist.id)
    db.close()
    assert processed.status == "complete"
    assert len(children) == 1
    assert children[0].media_item_id == items[0].id
    assert total == 1
    assert items[0].title == "Song"
    assert pending_work == 1
    assert scheduled.next_check_at is not None
