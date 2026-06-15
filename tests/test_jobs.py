"""Tests for shared job workflow functions used by API and CLI."""

import pytest

from media_dl.config import Config
from media_dl.jobs import (
    ClearHistoryBlockedError,
    UnsupportedUrlError,
    add_url,
    clear_history,
    import_queue_files,
    list_recent_jobs,
    retry_job,
    run_queued_jobs,
    skip_job,
)
from media_dl.db import Database
from media_dl.urltools import Source


def config(tmp_path):
    """Build a test config rooted in a temporary directory."""
    return Config(
        music_root=tmp_path / "music",
        state_dir=tmp_path / "state",
        queue_dir=tmp_path / "queue",
        download_dir=tmp_path / "downloads",
        yt_dlp_bin="false",
        spotdl_bin="false",
    )


def test_add_url_rejects_unsupported_url(tmp_path):
    """Verify unsupported URLs fail before job creation."""
    with pytest.raises(UnsupportedUrlError):
        add_url(config(tmp_path), "https://example.com/item")


def test_add_url_dedupes_by_default(tmp_path):
    """Verify normalized URLs dedupe by default."""
    cfg = config(tmp_path)

    first = add_url(cfg, "https://youtube.com/watch?v=abc", queue_only=True)
    second = add_url(
        cfg,
        "https://youtube.com/watch?v=abc&utm_source=test",
        queue_only=True,
    )

    assert first.created is True
    assert second.created is False
    assert first.job.id == second.job.id


def test_add_url_allows_duplicate(tmp_path):
    """Verify allow_duplicate creates a separate tracked job."""
    cfg = config(tmp_path)

    first = add_url(cfg, "https://youtube.com/watch?v=abc", queue_only=True)
    second = add_url(
        cfg,
        "https://youtube.com/watch?v=abc",
        queue_only=True,
        allow_duplicate=True,
    )

    assert first.created is True
    assert second.created is True
    assert second.job.id != first.job.id
    assert second.job.duplicate_of == first.job.id
    assert second.job.allow_duplicate is True


def test_add_url_queue_only_does_not_process(tmp_path):
    """Verify queue-only submissions skip immediate processing."""
    result = add_url(config(tmp_path), "https://youtube.com/watch?v=abc", queue_only=True)

    assert result.job.status == "queued"
    assert result.processed is None


def test_retry_job_queue_only_does_not_process(tmp_path):
    """Verify queue-only retries do not immediately run the downloader."""
    cfg = config(tmp_path)
    result = add_url(cfg, "https://youtube.com/watch?v=abc", queue_only=True)
    skipped = skip_job(cfg, result.job.id, "test")

    retried = retry_job(cfg, skipped.id, queue_only=True)

    assert retried.job.status == "retry"
    assert retried.processed is None


def test_skip_job_marks_skipped(tmp_path):
    """Verify skip stores status and reason."""
    cfg = config(tmp_path)
    result = add_url(cfg, "https://youtube.com/watch?v=abc", queue_only=True)

    skipped = skip_job(cfg, result.job.id, "bad")

    assert skipped.status == "skipped"
    assert skipped.last_error == "bad"


def test_list_recent_jobs_returns_newest_first(tmp_path):
    """Verify recent jobs are returned newest-first with pagination metadata."""
    cfg = config(tmp_path)
    first = add_url(cfg, "https://youtube.com/watch?v=abc", queue_only=True)
    second = add_url(cfg, "https://youtube.com/watch?v=def", queue_only=True)

    result = list_recent_jobs(cfg)

    assert [job.id for job in result.jobs] == [second.job.id, first.job.id]
    assert result.limit == 25
    assert result.offset == 0
    assert result.total == 2
    assert result.has_more is False


def test_import_queue_files(tmp_path):
    """Verify queue file import adds supported URLs and reports bad ones."""
    cfg = config(tmp_path)
    cfg.ensure_dirs()
    (cfg.queue_dir / "inbox.txt").write_text(
        "https://youtube.com/watch?v=abc\nhttps://example.com/nope\n",
        encoding="utf-8",
    )

    results = import_queue_files(cfg)

    assert results[0][0] is not None
    assert results[0][1] is True
    assert results[1][2] == "unsupported url: https://example.com/nope"


def test_run_queued_jobs(tmp_path):
    """Verify run_queued_jobs processes one queued job."""
    cfg = config(tmp_path)
    add_url(cfg, "https://youtube.com/watch?v=abc", queue_only=True)

    processed = run_queued_jobs(cfg, max_jobs=1)

    assert processed == 1
    assert list_recent_jobs(cfg).jobs[0].status == "failed"


def test_clear_history_deletes_jobs_and_archive(tmp_path):
    """Verify clear_history deletes job rows and the YouTube archive."""
    cfg = config(tmp_path)
    add_url(cfg, "https://youtube.com/watch?v=abc", queue_only=True)
    cfg.ensure_dirs()
    cfg.yt_archive_path.write_text("youtube abc\n", encoding="utf-8")

    result = clear_history(cfg)
    next_job = add_url(cfg, "https://youtube.com/watch?v=def", queue_only=True)

    assert result.deleted_jobs == 1
    assert result.archive_deleted is True
    assert not cfg.yt_archive_path.exists()
    assert next_job.job.id == 1


def test_clear_history_tolerates_missing_archive(tmp_path):
    """Verify clear_history succeeds when the archive file is absent."""
    cfg = config(tmp_path)
    add_url(cfg, "https://youtube.com/watch?v=abc", queue_only=True)

    result = clear_history(cfg)

    assert result.deleted_jobs == 1
    assert result.archive_deleted is False
    assert list_recent_jobs(cfg).total == 0


def test_clear_history_rejects_running_job(tmp_path):
    """Verify clear_history refuses to run while a job is running."""
    cfg = config(tmp_path)
    cfg.ensure_dirs()
    db = Database(cfg.db_path)
    job, _ = db.add_job(Source.YOUTUBE, "raw", "normalized")
    db.mark_running(job.id)
    db.close()

    with pytest.raises(ClearHistoryBlockedError):
        clear_history(cfg)
