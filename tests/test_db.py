from media_dl.db import Database
from media_dl.urltools import Source


def test_add_job_dedupes_by_normalized_url(tmp_path):
    db = Database(tmp_path / "state.sqlite")

    first, first_created = db.add_job(
        Source.YOUTUBE,
        "https://youtube.com/watch?v=abc",
        "https://youtube.com/watch?v=abc",
    )
    second, second_created = db.add_job(
        Source.YOUTUBE,
        "https://youtube.com/watch?v=abc&utm_source=x",
        "https://youtube.com/watch?v=abc",
    )

    assert first_created is True
    assert second_created is False
    assert first.id == second.id

    db.close()


def test_next_queued_and_retry(tmp_path):
    db = Database(tmp_path / "state.sqlite")
    job, _ = db.add_job(Source.SPOTIFY, "raw", "normalized")

    assert db.next_queued().id == job.id

    db.mark_running(job.id)
    db.mark_failed(job.id, "failed")
    assert db.next_queued() is None

    db.retry(job.id)
    assert db.next_queued().id == job.id

    db.close()


def test_skip_removes_job_from_queue(tmp_path):
    db = Database(tmp_path / "state.sqlite")
    job, _ = db.add_job(Source.YOUTUBE, "raw", "normalized")

    skipped = db.skip(job.id, "unavailable")

    assert skipped.status == "skipped"
    assert skipped.last_error == "unavailable"
    assert db.next_queued() is None

    db.close()
