"""Tests for SQLite job persistence, migrations, and state transitions."""

from media_dl.db import Database
from media_dl.urltools import Source


def test_add_job_dedupes_by_normalized_url(tmp_path):
    """Verify duplicate normalized URLs return the existing job."""
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
    """Verify forced duplicates get unique dedupe keys and original references."""
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


def test_add_job_stores_parent_id(tmp_path):
    """Verify child jobs can reference a playlist parent."""
    db = Database(tmp_path / "state.sqlite")
    parent, _ = db.add_job(
        Source.YOUTUBE,
        "https://youtube.com/playlist?list=PL",
        "https://youtube.com/playlist?list=PL",
    )

    child, created = db.add_job(
        Source.YOUTUBE,
        "https://youtube.com/watch?v=abc",
        "https://youtube.com/watch?v=abc",
        parent_id=parent.id,
    )

    assert created is True
    assert child.parent_id == parent.id

    db.close()


def test_migrates_old_unique_normalized_url_schema(tmp_path):
    """Verify very old unique-normalized-url databases are rebuilt safely."""
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
    assert migrated.last_warning is None
    assert migrated.parent_id is None
    assert migrated.child_count == 0

    db.close()


def test_migrates_schema_missing_last_warning(tmp_path):
    """Verify databases missing last_warning gain it during migration."""
    db_path = tmp_path / "state.sqlite"
    import sqlite3

    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            raw_url TEXT NOT NULL,
            normalized_url TEXT NOT NULL,
            dedupe_key TEXT NOT NULL,
            duplicate_of INTEGER,
            allow_duplicate INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL,
            started_at TEXT,
            finished_at TEXT,
            last_error TEXT,
            output_path TEXT
        );
        CREATE UNIQUE INDEX idx_jobs_dedupe_key ON jobs(dedupe_key);
        INSERT INTO jobs (
            source, raw_url, normalized_url, dedupe_key, status, first_seen, last_seen
        )
        VALUES (
            'youtube', 'raw', 'normalized', 'normalized', 'complete',
            '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00'
        );
        """
    )
    conn.commit()
    conn.close()

    db = Database(db_path)
    job = db.get_by_normalized_url("normalized")

    assert job.last_warning is None
    assert job.parent_id is None
    assert job.child_count == 0

    db.close()


def test_migrates_schema_missing_parent_child_fields(tmp_path):
    """Verify databases missing parent/child fields gain defaulted columns."""
    db_path = tmp_path / "state.sqlite"
    import sqlite3

    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            raw_url TEXT NOT NULL,
            normalized_url TEXT NOT NULL,
            dedupe_key TEXT NOT NULL,
            duplicate_of INTEGER,
            allow_duplicate INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL,
            started_at TEXT,
            finished_at TEXT,
            last_error TEXT,
            last_warning TEXT,
            output_path TEXT
        );
        CREATE UNIQUE INDEX idx_jobs_dedupe_key ON jobs(dedupe_key);
        INSERT INTO jobs (
            source, raw_url, normalized_url, dedupe_key, status, first_seen, last_seen
        )
        VALUES (
            'youtube', 'raw', 'normalized', 'normalized', 'complete',
            '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00'
        );
        """
    )
    conn.commit()
    conn.close()

    db = Database(db_path)
    job = db.get_by_normalized_url("normalized")

    assert job.parent_id is None
    assert job.child_count == 0
    assert job.child_created_count == 0
    assert job.child_duplicate_count == 0
    assert job.child_error_count == 0

    db.close()


def test_list_jobs_paginates_and_counts(tmp_path):
    """Verify job listing pagination and total counts."""
    db = Database(tmp_path / "state.sqlite")
    for index in range(5):
        db.add_job(Source.YOUTUBE, f"raw-{index}", f"normalized-{index}")

    page = db.list_jobs(limit=2, offset=2)

    assert db.count_jobs() == 5
    assert [job.id for job in page] == [3, 2]

    db.close()


def test_clear_jobs_deletes_rows_and_resets_ids(tmp_path):
    """Verify clear_jobs removes rows and resets the next job id."""
    db = Database(tmp_path / "state.sqlite")
    db.add_job(Source.YOUTUBE, "raw-1", "normalized-1")
    db.add_job(Source.YOUTUBE, "raw-2", "normalized-2")

    deleted = db.clear_jobs()
    next_job, created = db.add_job(Source.YOUTUBE, "raw-3", "normalized-3")

    assert deleted == 2
    assert db.count_jobs() == 1
    assert created is True
    assert next_job.id == 1

    db.close()


def test_clear_jobs_empty_database_returns_zero(tmp_path):
    """Verify clearing an empty database reports zero deleted jobs."""
    db = Database(tmp_path / "state.sqlite")

    deleted = db.clear_jobs()

    assert deleted == 0
    assert db.count_jobs() == 0

    db.close()


def test_next_queued_and_retry(tmp_path):
    """Verify queue selection ignores failed jobs until retry is requested."""
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
    """Verify skipped jobs leave the runnable queue."""
    db = Database(tmp_path / "state.sqlite")
    job, _ = db.add_job(Source.YOUTUBE, "raw", "normalized")

    skipped = db.skip(job.id, "unavailable")

    assert skipped.status == "skipped"
    assert skipped.last_error == "unavailable"
    assert db.next_queued() is None

    db.close()


def test_mark_complete_persists_warning(tmp_path):
    """Verify completed jobs can store non-fatal warnings."""
    db = Database(tmp_path / "state.sqlite")
    job, _ = db.add_job(Source.YOUTUBE, "raw", "normalized")

    db.mark_running(job.id)
    db.mark_complete(job.id, "output", warning="Some playlist items failed.")
    completed = db.get_job(job.id)

    assert completed.status == "complete"
    assert completed.last_warning == "Some playlist items failed."
    assert completed.last_error is None

    db.close()


def test_mark_expanded_persists_child_counts(tmp_path):
    """Verify playlist parent summary counts are persisted."""
    db = Database(tmp_path / "state.sqlite")
    job, _ = db.add_job(Source.YOUTUBE, "raw", "normalized")

    db.mark_running(job.id)
    db.mark_expanded(
        job.id,
        child_count=3,
        child_created_count=2,
        child_duplicate_count=1,
        child_error_count=0,
        warning="Expanded playlist: 2 queued, 1 duplicates, 0 errors.",
    )
    expanded = db.get_job(job.id)

    assert expanded.status == "complete"
    assert expanded.child_count == 3
    assert expanded.child_created_count == 2
    assert expanded.child_duplicate_count == 1
    assert expanded.child_error_count == 0
    assert expanded.last_warning == "Expanded playlist: 2 queued, 1 duplicates, 0 errors."

    db.close()


def test_playlist_reconciliation_tracks_shared_and_removed_items(tmp_path):
    """Verify canonical items can be shared and membership removal is non-destructive."""
    from media_dl.playlist import PlaylistEntry

    db = Database(tmp_path / "state.sqlite")
    first_job, _ = db.add_job(
        Source.YOUTUBE,
        "https://youtube.com/playlist?list=one",
        "https://youtube.com/playlist?list=one",
    )
    second_job, _ = db.add_job(
        Source.YOUTUBE,
        "https://youtube.com/playlist?list=two",
        "https://youtube.com/playlist?list=two",
    )
    first = db.ensure_playlist(first_job)
    second = db.ensure_playlist(second_job)
    entry = PlaylistEntry(
        source=Source.YOUTUBE,
        provider_id="abc",
        url="https://www.youtube.com/watch?v=abc",
        title="Song",
        artist="Artist",
        position=1,
    )

    db.reconcile_playlist_items(first.id, [entry])
    db.reconcile_playlist_items(second.id, [entry])
    first_items, _ = db.list_playlist_items(first.id)
    second_items, _ = db.list_playlist_items(second.id)
    assert first_items[0].id == second_items[0].id

    db.reconcile_playlist_items(first.id, [])
    removed, _ = db.list_playlist_items(first.id)
    assert removed[0].active is False
    assert db.list_playlist_items(second.id)[0][0].active is True
    db.close()


def test_clear_jobs_preserves_tracked_playlist_and_inventory(tmp_path):
    """Verify history cleanup retains tracking definitions and current membership."""
    from media_dl.playlist import PlaylistEntry

    db = Database(tmp_path / "state.sqlite")
    job, _ = db.add_job(
        Source.SPOTIFY,
        "https://open.spotify.com/playlist/abc",
        "https://open.spotify.com/playlist/abc",
    )
    playlist = db.ensure_playlist(job, tracked=True)
    db.reconcile_playlist_items(
        playlist.id,
        [
            PlaylistEntry(
                source=Source.SPOTIFY,
                provider_id="track",
                url="https://open.spotify.com/track/track",
                title="Song",
                artist="Artist",
                position=1,
            )
        ],
    )

    assert db.clear_jobs() == 1
    preserved = db.get_playlist(playlist.id)
    assert preserved.tracked is True
    assert preserved.job_id is None
    assert db.list_playlist_items(playlist.id)[1] == 1
    db.close()


def test_persistent_work_dedupes_and_recovers(tmp_path):
    """Verify one active work request per job and restart recovery."""
    db = Database(tmp_path / "state.sqlite")
    job, _ = db.add_job(Source.YOUTUBE, "raw", "normalized")
    assert db.enqueue_work(job.id) is True
    assert db.enqueue_work(job.id) is False

    work = db.claim_work()
    assert work is not None
    assert work.job_id == job.id
    db.mark_running(job.id)
    assert db.recover_work() == 1
    assert db.get_job(job.id).status == "retry"
    recovered = db.claim_work()
    assert recovered is not None
    assert recovered.id == work.id
    db.finish_work(recovered.id)
    db.close()
