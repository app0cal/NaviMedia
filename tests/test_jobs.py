import pytest

from media_dl.config import Config
from media_dl.jobs import (
    UnsupportedUrlError,
    add_url,
    import_queue_files,
    list_recent_jobs,
    retry_job,
    run_queued_jobs,
    skip_job,
)


def config(tmp_path):
    return Config(
        music_root=tmp_path / "music",
        state_dir=tmp_path / "state",
        queue_dir=tmp_path / "queue",
        download_dir=tmp_path / "downloads",
        yt_dlp_bin="false",
        spotdl_bin="false",
    )


def test_add_url_rejects_unsupported_url(tmp_path):
    with pytest.raises(UnsupportedUrlError):
        add_url(config(tmp_path), "https://example.com/item")


def test_add_url_dedupes_by_default(tmp_path):
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
    result = add_url(config(tmp_path), "https://youtube.com/watch?v=abc", queue_only=True)

    assert result.job.status == "queued"
    assert result.processed is None


def test_retry_job_queue_only_does_not_process(tmp_path):
    cfg = config(tmp_path)
    result = add_url(cfg, "https://youtube.com/watch?v=abc", queue_only=True)
    skipped = skip_job(cfg, result.job.id, "test")

    retried = retry_job(cfg, skipped.id, queue_only=True)

    assert retried.job.status == "retry"
    assert retried.processed is None


def test_skip_job_marks_skipped(tmp_path):
    cfg = config(tmp_path)
    result = add_url(cfg, "https://youtube.com/watch?v=abc", queue_only=True)

    skipped = skip_job(cfg, result.job.id, "bad")

    assert skipped.status == "skipped"
    assert skipped.last_error == "bad"


def test_list_recent_jobs_returns_newest_first(tmp_path):
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
    cfg = config(tmp_path)
    add_url(cfg, "https://youtube.com/watch?v=abc", queue_only=True)

    processed = run_queued_jobs(cfg, max_jobs=1)

    assert processed == 1
    assert list_recent_jobs(cfg).jobs[0].status == "failed"
