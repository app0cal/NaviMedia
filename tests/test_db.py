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
    assert first.dedupe_key == "https://youtube.com/watch?v=abc"
    assert first.allow_duplicate is False

    db.close()


def test_add_job_allows_forced_duplicate(tmp_path):
    db = Database(tmp_path / "state.sqlite")

    first, first_created = db.add_job(
        Source.YOUTUBE,
        "https://youtube.com/watch?v=abc",
        "https://youtube.com/watch?v=abc",
    )
    second, second_created = db.add_job(
        Source.YOUTUBE,
        "https://youtube.com/watch?v=abc",
        "https://youtube.com/watch?v=abc",
        allow_duplicate=True,
    )

    assert first_created is True
    assert second_created is True
    assert first.id != second.id
    assert second.normalized_url == first.normalized_url
    assert second.dedupe_key != first.dedupe_key
    assert second.duplicate_of == first.id
    assert second.allow_duplicate is True

    db.close()


def test_migrates_old_unique_normalized_url_schema(tmp_path):
    db_path = tmp_path / "state.sqlite"
    import sqlite3

    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            raw_url TEXT NOT NULL,
            normalized_url TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL,
            started_at TEXT,
            finished_at TEXT,
            last_error TEXT,
            output_path TEXT
        );
        INSERT INTO jobs (
            source, raw_url, normalized_url, status, first_seen, last_seen
        )
        VALUES (
            'youtube', 'raw', 'normalized', 'complete',
            '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00'
        );
        """
    )
    conn.commit()
    conn.close()

    db = Database(db_path)
    migrated = db.get_by_normalized_url("normalized")
    duplicate, created = db.add_job(Source.YOUTUBE, "raw", "normalized", allow_duplicate=True)

    assert migrated.dedupe_key == "normalized"
    assert migrated.allow_duplicate is False
    assert created is True
    assert duplicate.duplicate_of == migrated.id

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
