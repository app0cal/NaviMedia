"""Manage SQLite job persistence, schema migrations, and job state transitions."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Iterable
from uuid import uuid4

from media_dl.urltools import Source


@dataclass(frozen=True)
class Job:
    """Represent one persisted download or playlist-expansion job."""

    id: int
    source: Source
    raw_url: str
    normalized_url: str
    status: str
    attempts: int
    last_error: str | None
    last_warning: str | None
    output_path: str | None
    dedupe_key: str
    duplicate_of: int | None
    allow_duplicate: bool
    parent_id: int | None = None
    child_count: int = 0
    child_created_count: int = 0
    child_duplicate_count: int = 0
    child_error_count: int = 0
    playlist_id: int | None = None
    media_item_id: int | None = None
    job_kind: str = "download"


@dataclass(frozen=True)
class Playlist:
    """Represent one inventoried playlist and its optional tracking schedule."""

    id: int
    source: Source
    raw_url: str
    normalized_url: str
    title: str | None
    job_id: int | None
    tracked: bool
    paused: bool
    interval_seconds: int
    last_checked_at: str | None
    last_success_at: str | None
    next_check_at: str | None
    last_error: str | None


@dataclass(frozen=True)
class PlaylistItem:
    """Represent a canonical media item in one playlist membership."""

    id: int
    playlist_id: int
    source: Source
    provider_id: str
    url: str
    title: str
    artist: str
    position: int
    active: bool
    download_status: str
    output_path: str | None
    last_error: str | None
    download_job_id: int | None


@dataclass(frozen=True)
class WorkItem:
    """Represent one persistent request for the service background worker."""

    id: int
    job_id: int
    status: str
    created_at: str


class Database:
    """Wrap SQLite access for jobs while keeping callers on dataclass objects."""

    def __init__(self, path: Path):
        """Open the database file and ensure the current schema exists."""
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.migrate()

    def close(self) -> None:
        """Close the SQLite connection."""
        self.conn.close()

    def migrate(self) -> None:
        """Create or upgrade the jobs table to the current schema."""
        row = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'jobs'"
        ).fetchone()
        if row is None:
            self._create_schema()
            return

        table_sql = self.conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'jobs'"
        ).fetchone()["sql"]
        columns = {
            row["name"]
            for row in self.conn.execute("PRAGMA table_info(jobs)").fetchall()
        }
        needs_rebuild = (
            "normalized_url TEXT NOT NULL UNIQUE" in table_sql
            or "dedupe_key" not in columns
            or "duplicate_of" not in columns
            or "allow_duplicate" not in columns
        )
        if needs_rebuild:
            self._rebuild_jobs_table(columns)
        else:
            self._add_missing_columns(columns)
        self._create_indexes()
        self._create_feature_schema()
        self.conn.commit()

    def _create_schema(self) -> None:
        """Create a new jobs table and indexes."""
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS jobs (
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
                output_path TEXT,
                parent_id INTEGER,
                child_count INTEGER NOT NULL DEFAULT 0,
                child_created_count INTEGER NOT NULL DEFAULT 0,
                child_duplicate_count INTEGER NOT NULL DEFAULT 0,
                child_error_count INTEGER NOT NULL DEFAULT 0,
                playlist_id INTEGER,
                media_item_id INTEGER,
                job_kind TEXT NOT NULL DEFAULT 'download'
            );
            """
        )
        self._create_indexes()
        self._create_feature_schema()
        self.conn.commit()

    def _create_indexes(self) -> None:
        """Create indexes used by dedupe, queue lookup, filtering, and parent links."""
        self.conn.executescript(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_dedupe_key ON jobs(dedupe_key);
            CREATE INDEX IF NOT EXISTS idx_jobs_normalized_url ON jobs(normalized_url);
            CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
            CREATE INDEX IF NOT EXISTS idx_jobs_source ON jobs(source);
            CREATE INDEX IF NOT EXISTS idx_jobs_parent_id ON jobs(parent_id);
            CREATE INDEX IF NOT EXISTS idx_jobs_playlist_id ON jobs(playlist_id);
            CREATE INDEX IF NOT EXISTS idx_jobs_media_item_id ON jobs(media_item_id);
            """
        )

    def _create_feature_schema(self) -> None:
        """Create additive playlist, membership, and background-work tables."""
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS playlists (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                raw_url TEXT NOT NULL,
                normalized_url TEXT NOT NULL UNIQUE,
                title TEXT,
                job_id INTEGER,
                tracked INTEGER NOT NULL DEFAULT 0,
                paused INTEGER NOT NULL DEFAULT 0,
                interval_seconds INTEGER NOT NULL DEFAULT 604800,
                last_checked_at TEXT,
                last_success_at TEXT,
                next_check_at TEXT,
                last_error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_playlists_tracking
                ON playlists(tracked, paused, next_check_at);

            CREATE TABLE IF NOT EXISTS media_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                provider_id TEXT NOT NULL,
                normalized_url TEXT NOT NULL,
                title TEXT NOT NULL,
                artist TEXT NOT NULL,
                download_status TEXT NOT NULL DEFAULT 'discovered',
                output_path TEXT,
                last_error TEXT,
                download_job_id INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(source, provider_id)
            );
            CREATE INDEX IF NOT EXISTS idx_media_items_url ON media_items(normalized_url);
            CREATE INDEX IF NOT EXISTS idx_media_items_job ON media_items(download_job_id);

            CREATE TABLE IF NOT EXISTS playlist_memberships (
                playlist_id INTEGER NOT NULL,
                media_item_id INTEGER NOT NULL,
                position INTEGER NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                PRIMARY KEY (playlist_id, media_item_id)
            );
            CREATE INDEX IF NOT EXISTS idx_memberships_playlist
                ON playlist_memberships(playlist_id, active, position);

            CREATE TABLE IF NOT EXISTS work_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT,
                last_error TEXT
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_work_active_job
                ON work_items(job_id) WHERE status IN ('pending', 'running');
            CREATE INDEX IF NOT EXISTS idx_work_pending
                ON work_items(status, id);
            """
        )

    def _add_missing_columns(self, columns: set[str]) -> None:
        """Add nullable/defaulted columns for incremental schema upgrades."""
        if "last_warning" not in columns:
            self.conn.execute("ALTER TABLE jobs ADD COLUMN last_warning TEXT")
        if "parent_id" not in columns:
            self.conn.execute("ALTER TABLE jobs ADD COLUMN parent_id INTEGER")
        if "child_count" not in columns:
            self.conn.execute("ALTER TABLE jobs ADD COLUMN child_count INTEGER NOT NULL DEFAULT 0")
        if "child_created_count" not in columns:
            self.conn.execute(
                "ALTER TABLE jobs ADD COLUMN child_created_count INTEGER NOT NULL DEFAULT 0"
            )
        if "child_duplicate_count" not in columns:
            self.conn.execute(
                "ALTER TABLE jobs ADD COLUMN child_duplicate_count INTEGER NOT NULL DEFAULT 0"
            )
        if "child_error_count" not in columns:
            self.conn.execute(
                "ALTER TABLE jobs ADD COLUMN child_error_count INTEGER NOT NULL DEFAULT 0"
            )
        if "playlist_id" not in columns:
            self.conn.execute("ALTER TABLE jobs ADD COLUMN playlist_id INTEGER")
        if "media_item_id" not in columns:
            self.conn.execute("ALTER TABLE jobs ADD COLUMN media_item_id INTEGER")
        if "job_kind" not in columns:
            self.conn.execute(
                "ALTER TABLE jobs ADD COLUMN job_kind TEXT NOT NULL DEFAULT 'download'"
            )

    def _rebuild_jobs_table(self, columns: set[str]) -> None:
        """Rebuild old incompatible jobs tables while preserving known data."""
        self.conn.executescript(
            """
            DROP TABLE IF EXISTS jobs_new;
            CREATE TABLE jobs_new (
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
                output_path TEXT,
                parent_id INTEGER,
                child_count INTEGER NOT NULL DEFAULT 0,
                child_created_count INTEGER NOT NULL DEFAULT 0,
                child_duplicate_count INTEGER NOT NULL DEFAULT 0,
                child_error_count INTEGER NOT NULL DEFAULT 0,
                playlist_id INTEGER,
                media_item_id INTEGER,
                job_kind TEXT NOT NULL DEFAULT 'download'
            );
            """
        )

        has_dedupe = "dedupe_key" in columns
        has_duplicate_of = "duplicate_of" in columns
        has_allow_duplicate = "allow_duplicate" in columns
        has_last_warning = "last_warning" in columns
        has_parent_id = "parent_id" in columns
        has_child_count = "child_count" in columns
        has_child_created_count = "child_created_count" in columns
        has_child_duplicate_count = "child_duplicate_count" in columns
        has_child_error_count = "child_error_count" in columns
        has_playlist_id = "playlist_id" in columns
        has_media_item_id = "media_item_id" in columns
        has_job_kind = "job_kind" in columns
        # Old databases used normalized_url as the unique key; current databases use
        # dedupe_key so forced duplicates can coexist with their original URL.
        dedupe_expr = "dedupe_key" if has_dedupe else "normalized_url"
        duplicate_expr = "duplicate_of" if has_duplicate_of else "NULL"
        allow_expr = "allow_duplicate" if has_allow_duplicate else "0"
        warning_expr = "last_warning" if has_last_warning else "NULL"
        parent_expr = "parent_id" if has_parent_id else "NULL"
        child_count_expr = "child_count" if has_child_count else "0"
        child_created_expr = "child_created_count" if has_child_created_count else "0"
        child_duplicate_expr = "child_duplicate_count" if has_child_duplicate_count else "0"
        child_error_expr = "child_error_count" if has_child_error_count else "0"
        playlist_expr = "playlist_id" if has_playlist_id else "NULL"
        media_item_expr = "media_item_id" if has_media_item_id else "NULL"
        job_kind_expr = "job_kind" if has_job_kind else "'download'"

        self.conn.execute(
            f"""
            INSERT INTO jobs_new (
                id, source, raw_url, normalized_url, dedupe_key, duplicate_of,
                allow_duplicate, status, attempts, first_seen, last_seen,
                started_at, finished_at, last_error, last_warning, output_path,
                parent_id, child_count, child_created_count, child_duplicate_count,
                child_error_count, playlist_id, media_item_id, job_kind
            )
            SELECT
                id, source, raw_url, normalized_url, {dedupe_expr}, {duplicate_expr},
                {allow_expr}, status, attempts, first_seen, last_seen,
                started_at, finished_at, last_error, {warning_expr}, output_path,
                {parent_expr}, {child_count_expr}, {child_created_expr},
                {child_duplicate_expr}, {child_error_expr}, {playlist_expr},
                {media_item_expr}, {job_kind_expr}
            FROM jobs
            """
        )
        self.conn.executescript(
            """
            DROP TABLE jobs;
            ALTER TABLE jobs_new RENAME TO jobs;
            """
        )

    def add_job(
        self,
        source: Source,
        raw_url: str,
        normalized_url: str,
        allow_duplicate: bool = False,
        parent_id: int | None = None,
        playlist_id: int | None = None,
        media_item_id: int | None = None,
        job_kind: str = "download",
    ) -> tuple[Job, bool]:
        """Insert a queued job or return the existing deduped job."""
        now = _now()
        duplicate_of = self._first_job_id_for_url(normalized_url) if allow_duplicate else None
        dedupe_key = (
            f"{normalized_url}#duplicate:{uuid4().hex}"
            if allow_duplicate
            else normalized_url
        )
        try:
            cur = self.conn.execute(
                """
                INSERT INTO jobs (
                    source, raw_url, normalized_url, dedupe_key, duplicate_of,
                    allow_duplicate, parent_id, playlist_id, media_item_id, job_kind,
                    status, first_seen, last_seen
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?)
                """,
                (
                    source.value,
                    raw_url,
                    normalized_url,
                    dedupe_key,
                    duplicate_of,
                    1 if allow_duplicate else 0,
                    parent_id,
                    playlist_id,
                    media_item_id,
                    job_kind,
                    now,
                    now,
                ),
            )
            self.conn.commit()
            return self.get_job(cur.lastrowid), True
        except sqlite3.IntegrityError:
            if allow_duplicate:
                raise
            self.conn.execute(
                """
                UPDATE jobs
                SET last_seen = ?,
                    playlist_id = COALESCE(playlist_id, ?),
                    media_item_id = COALESCE(media_item_id, ?)
                WHERE dedupe_key = ?
                """,
                (now, playlist_id, media_item_id, normalized_url),
            )
            self.conn.commit()
            return self.get_by_dedupe_key(normalized_url), False

    def get_job(self, job_id: int) -> Job:
        """Fetch one job by id or raise KeyError when absent."""
        row = self.conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(f"job {job_id} not found")
        return _row_to_job(row)

    def get_by_normalized_url(self, normalized_url: str) -> Job:
        """Fetch the canonical job for a normalized URL."""
        row = self.conn.execute(
            """
            SELECT * FROM jobs
            WHERE normalized_url = ?
            ORDER BY allow_duplicate ASC, id ASC
            LIMIT 1
            """,
            (normalized_url,),
        ).fetchone()
        if row is None:
            raise KeyError(f"url {normalized_url} not found")
        return _row_to_job(row)

    def get_by_dedupe_key(self, dedupe_key: str) -> Job:
        """Fetch one job by its unique dedupe key."""
        row = self.conn.execute(
            "SELECT * FROM jobs WHERE dedupe_key = ?",
            (dedupe_key,),
        ).fetchone()
        if row is None:
            raise KeyError(f"dedupe key {dedupe_key} not found")
        return _row_to_job(row)

    def _first_job_id_for_url(self, normalized_url: str) -> int | None:
        """Return the original job id for a URL so forced duplicates can reference it."""
        row = self.conn.execute(
            """
            SELECT id FROM jobs
            WHERE normalized_url = ?
            ORDER BY allow_duplicate ASC, id ASC
            LIMIT 1
            """,
            (normalized_url,),
        ).fetchone()
        return row["id"] if row else None

    def count_jobs(self, statuses: Iterable[str] | None = None) -> int:
        """Count all jobs or only jobs matching selected statuses."""
        if statuses:
            status_list = list(statuses)
            placeholders = ",".join("?" for _ in status_list)
            row = self.conn.execute(
                f"SELECT COUNT(*) AS total FROM jobs WHERE status IN ({placeholders})",
                tuple(status_list),
            ).fetchone()
        else:
            row = self.conn.execute("SELECT COUNT(*) AS total FROM jobs").fetchone()
        return int(row["total"])

    def clear_jobs(self) -> int:
        """Delete job history while preserving active tracking definitions."""
        deleted = self.count_jobs()
        with self.conn:
            self.conn.execute("DELETE FROM work_items")
            self.conn.execute(
                "DELETE FROM playlist_memberships WHERE playlist_id IN "
                "(SELECT id FROM playlists WHERE tracked = 0)"
            )
            self.conn.execute("DELETE FROM playlists WHERE tracked = 0")
            self.conn.execute(
                "UPDATE playlists SET job_id = NULL WHERE tracked = 1"
            )
            self.conn.execute(
                "UPDATE media_items SET download_job_id = NULL"
            )
            self.conn.execute("DELETE FROM jobs")
            self.conn.execute("DELETE FROM sqlite_sequence WHERE name = 'jobs'")
            self.conn.execute(
                """
                DELETE FROM media_items
                WHERE id NOT IN (SELECT media_item_id FROM playlist_memberships)
                """
            )
        return deleted

    def list_jobs(
        self,
        statuses: Iterable[str] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Job]:
        """List jobs newest-first, optionally constrained to selected statuses."""
        if statuses:
            status_list = list(statuses)
            placeholders = ",".join("?" for _ in status_list)
            rows = self.conn.execute(
                f"""
                SELECT * FROM jobs
                WHERE status IN ({placeholders})
                ORDER BY id DESC
                LIMIT ? OFFSET ?
                """,
                (*status_list, limit, offset),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM jobs ORDER BY id DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return [_row_to_job(row) for row in rows]

    def next_queued(self) -> Job | None:
        """Return the oldest queued or retry job for manual processing."""
        row = self.conn.execute(
            """
            SELECT * FROM jobs
            WHERE status IN ('queued', 'retry')
            ORDER BY id ASC
            LIMIT 1
            """
        ).fetchone()
        return _row_to_job(row) if row else None

    def mark_running(self, job_id: int) -> None:
        """Mark a job running and increment its attempt counter."""
        self.conn.execute(
            """
            UPDATE jobs
            SET status = 'running',
                attempts = attempts + 1,
                started_at = ?,
                last_error = NULL,
                last_warning = NULL
            WHERE id = ?
            """,
            (_now(), job_id),
        )
        self.conn.commit()

    def mark_complete(self, job_id: int, output_path: str | None, warning: str | None = None) -> None:
        """Mark a job complete with its output path and optional warning."""
        self.conn.execute(
            """
            UPDATE jobs
            SET status = 'complete',
                finished_at = ?,
                output_path = ?,
                last_error = NULL,
                last_warning = ?
            WHERE id = ?
            """,
            (_now(), output_path, warning[-4000:] if warning else None, job_id),
        )
        self.conn.commit()

    def mark_expanded(
        self,
        job_id: int,
        child_count: int,
        child_created_count: int,
        child_duplicate_count: int,
        child_error_count: int,
        warning: str,
    ) -> None:
        """Mark a playlist parent complete with child expansion counts."""
        self.conn.execute(
            """
            UPDATE jobs
            SET status = 'complete',
                finished_at = ?,
                output_path = NULL,
                last_error = NULL,
                last_warning = ?,
                child_count = ?,
                child_created_count = ?,
                child_duplicate_count = ?,
                child_error_count = ?
            WHERE id = ?
            """,
            (
                _now(),
                warning[-4000:],
                child_count,
                child_created_count,
                child_duplicate_count,
                child_error_count,
                job_id,
            ),
        )
        self.conn.commit()

    def mark_failed(self, job_id: int, error: str) -> None:
        """Mark a job failed with the final error text."""
        self.conn.execute(
            """
            UPDATE jobs
            SET status = 'failed',
                finished_at = ?,
                last_error = ?,
                last_warning = NULL
            WHERE id = ?
            """,
            (_now(), error[-4000:], job_id),
        )
        self.conn.commit()

    def retry(self, job_id: int) -> Job:
        """Move a job back to retry status and clear prior error/warning text."""
        self.conn.execute(
            "UPDATE jobs SET status = 'retry', last_error = NULL, last_warning = NULL WHERE id = ?",
            (job_id,),
        )
        self.conn.commit()
        return self.get_job(job_id)

    def skip(self, job_id: int, reason: str | None = None) -> Job:
        """Mark a job skipped with an optional human reason."""
        self.conn.execute(
            """
            UPDATE jobs
            SET status = 'skipped',
                finished_at = ?,
                last_error = ?,
                last_warning = NULL
            WHERE id = ?
            """,
            (_now(), reason, job_id),
        )
        self.conn.commit()
        return self.get_job(job_id)

    def ensure_playlist(
        self,
        job: Job,
        *,
        tracked: bool = False,
        interval_seconds: int = 604800,
    ) -> Playlist:
        """Create or update the playlist record associated with a parent job."""
        now = _now()
        self.conn.execute(
            """
            INSERT INTO playlists (
                source, raw_url, normalized_url, job_id, tracked, paused,
                interval_seconds, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?)
            ON CONFLICT(normalized_url) DO UPDATE SET
                raw_url = excluded.raw_url,
                job_id = excluded.job_id,
                tracked = CASE
                    WHEN excluded.tracked = 1 THEN 1 ELSE playlists.tracked
                END,
                interval_seconds = CASE
                    WHEN excluded.tracked = 1 THEN excluded.interval_seconds
                    ELSE playlists.interval_seconds
                END,
                updated_at = excluded.updated_at
            """,
            (
                job.source.value,
                job.raw_url,
                job.normalized_url,
                job.id,
                1 if tracked else 0,
                interval_seconds,
                now,
                now,
            ),
        )
        row = self.conn.execute(
            "SELECT * FROM playlists WHERE normalized_url = ?",
            (job.normalized_url,),
        ).fetchone()
        assert row is not None
        self.conn.execute(
            """
            UPDATE jobs
            SET playlist_id = ?, job_kind = 'playlist'
            WHERE id = ?
            """,
            (row["id"], job.id),
        )
        self.conn.commit()
        return _row_to_playlist(row)

    def get_playlist(self, playlist_id: int) -> Playlist:
        """Fetch one playlist or raise KeyError when it is absent."""
        row = self.conn.execute(
            "SELECT * FROM playlists WHERE id = ?",
            (playlist_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"playlist {playlist_id} not found")
        return _row_to_playlist(row)

    def get_playlist_for_job(self, job_id: int) -> Playlist | None:
        """Return the playlist associated with a parent or child job."""
        row = self.conn.execute(
            """
            SELECT playlists.*
            FROM jobs
            JOIN playlists ON playlists.id = jobs.playlist_id
            WHERE jobs.id = ?
            """,
            (job_id,),
        ).fetchone()
        return _row_to_playlist(row) if row else None

    def get_playlist_by_url(self, normalized_url: str) -> Playlist | None:
        """Return one playlist by its normalized provider URL."""
        row = self.conn.execute(
            "SELECT * FROM playlists WHERE normalized_url = ?",
            (normalized_url,),
        ).fetchone()
        return _row_to_playlist(row) if row else None

    def list_tracked_playlists(self) -> list[Playlist]:
        """List tracked playlists with the most recently updated first."""
        rows = self.conn.execute(
            """
            SELECT * FROM playlists
            WHERE tracked = 1
            ORDER BY updated_at DESC, id DESC
            """
        ).fetchall()
        return [_row_to_playlist(row) for row in rows]

    def update_playlist_tracking(
        self,
        playlist_id: int,
        *,
        tracked: bool | None = None,
        paused: bool | None = None,
        interval_seconds: int | None = None,
    ) -> Playlist:
        """Update tracking controls while retaining the latest inventory."""
        playlist = self.get_playlist(playlist_id)
        next_tracked = playlist.tracked if tracked is None else tracked
        next_paused = playlist.paused if paused is None else paused
        next_interval = (
            playlist.interval_seconds if interval_seconds is None else interval_seconds
        )
        next_check = playlist.next_check_at
        if not next_tracked:
            next_paused = False
            next_check = None
        elif next_check is None:
            next_check = _now()
        self.conn.execute(
            """
            UPDATE playlists
            SET tracked = ?, paused = ?, interval_seconds = ?,
                next_check_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                1 if next_tracked else 0,
                1 if next_paused else 0,
                next_interval,
                next_check,
                _now(),
                playlist_id,
            ),
        )
        self.conn.commit()
        return self.get_playlist(playlist_id)

    def mark_playlist_check_success(
        self,
        playlist_id: int,
        *,
        title: str | None,
    ) -> Playlist:
        """Record a successful inventory and calculate the next interval."""
        playlist = self.get_playlist(playlist_id)
        now = datetime.now(UTC)
        next_check = (
            (now + timedelta(seconds=playlist.interval_seconds)).isoformat(timespec="seconds")
            if playlist.tracked
            else None
        )
        self.conn.execute(
            """
            UPDATE playlists
            SET title = COALESCE(?, title),
                last_checked_at = ?,
                last_success_at = ?,
                next_check_at = ?,
                last_error = NULL,
                updated_at = ?
            WHERE id = ?
            """,
            (
                title,
                now.isoformat(timespec="seconds"),
                now.isoformat(timespec="seconds"),
                next_check,
                now.isoformat(timespec="seconds"),
                playlist_id,
            ),
        )
        self.conn.commit()
        return self.get_playlist(playlist_id)

    def mark_playlist_check_failed(self, playlist_id: int, error: str) -> Playlist:
        """Record an inventory failure without changing the last good membership."""
        playlist = self.get_playlist(playlist_id)
        now = datetime.now(UTC)
        next_check = (
            (now + timedelta(seconds=playlist.interval_seconds)).isoformat(timespec="seconds")
            if playlist.tracked
            else None
        )
        self.conn.execute(
            """
            UPDATE playlists
            SET last_checked_at = ?, next_check_at = ?, last_error = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                now.isoformat(timespec="seconds"),
                next_check,
                error[-4000:],
                now.isoformat(timespec="seconds"),
                playlist_id,
            ),
        )
        self.conn.commit()
        return self.get_playlist(playlist_id)

    def due_playlists(self, now: str | None = None) -> list[Playlist]:
        """Return active tracked playlists whose next check is due."""
        rows = self.conn.execute(
            """
            SELECT * FROM playlists
            WHERE tracked = 1
              AND paused = 0
              AND next_check_at IS NOT NULL
              AND next_check_at <= ?
            ORDER BY next_check_at ASC
            """,
            (now or _now(),),
        ).fetchall()
        return [_row_to_playlist(row) for row in rows]

    def attach_playlist_job(self, playlist_id: int, job_id: int) -> None:
        """Attach a recreated parent job to an existing playlist."""
        with self.conn:
            self.conn.execute(
                "UPDATE playlists SET job_id = ?, updated_at = ? WHERE id = ?",
                (job_id, _now(), playlist_id),
            )
            self.conn.execute(
                "UPDATE jobs SET playlist_id = ?, job_kind = 'playlist' WHERE id = ?",
                (playlist_id, job_id),
            )

    def reconcile_playlist_items(self, playlist_id: int, entries: Iterable[object]) -> None:
        """Replace current membership with one successfully extracted snapshot."""
        now = _now()
        entries = list(entries)
        with self.conn:
            self.conn.execute(
                "UPDATE playlist_memberships SET active = 0 WHERE playlist_id = ?",
                (playlist_id,),
            )
            for entry in entries:
                provider_id = str(getattr(entry, "provider_id"))
                source = getattr(entry, "source")
                source_value = source.value if isinstance(source, Source) else str(source)
                url = str(getattr(entry, "url"))
                title = str(getattr(entry, "title"))
                artist = str(getattr(entry, "artist"))
                position = int(getattr(entry, "position"))
                self.conn.execute(
                    """
                    INSERT INTO media_items (
                        source, provider_id, normalized_url, title, artist,
                        created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(source, provider_id) DO UPDATE SET
                        normalized_url = excluded.normalized_url,
                        title = excluded.title,
                        artist = excluded.artist,
                        updated_at = excluded.updated_at
                    """,
                    (source_value, provider_id, url, title, artist, now, now),
                )
                media_row = self.conn.execute(
                    """
                    SELECT id FROM media_items
                    WHERE source = ? AND provider_id = ?
                    """,
                    (source_value, provider_id),
                ).fetchone()
                assert media_row is not None
                self.conn.execute(
                    """
                    INSERT INTO playlist_memberships (
                        playlist_id, media_item_id, position, active,
                        first_seen_at, last_seen_at
                    )
                    VALUES (?, ?, ?, 1, ?, ?)
                    ON CONFLICT(playlist_id, media_item_id) DO UPDATE SET
                        position = excluded.position,
                        active = 1,
                        last_seen_at = excluded.last_seen_at
                    """,
                    (playlist_id, media_row["id"], position, now, now),
                )

    def list_playlist_items(
        self,
        playlist_id: int,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[PlaylistItem], int]:
        """Return one ordered page of current and removed playlist items."""
        self.get_playlist(playlist_id)
        total_row = self.conn.execute(
            "SELECT COUNT(*) AS total FROM playlist_memberships WHERE playlist_id = ?",
            (playlist_id,),
        ).fetchone()
        rows = self.conn.execute(
            """
            SELECT media_items.*, playlist_memberships.playlist_id,
                   playlist_memberships.position, playlist_memberships.active
            FROM playlist_memberships
            JOIN media_items ON media_items.id = playlist_memberships.media_item_id
            WHERE playlist_memberships.playlist_id = ?
            ORDER BY playlist_memberships.active DESC,
                     playlist_memberships.position ASC,
                     media_items.id ASC
            LIMIT ? OFFSET ?
            """,
            (playlist_id, limit, offset),
        ).fetchall()
        return ([_row_to_playlist_item(row) for row in rows], int(total_row["total"]))

    def link_media_download_job(self, media_item_id: int, job_id: int) -> None:
        """Associate a canonical media item with its download job."""
        with self.conn:
            self.conn.execute(
                """
                UPDATE media_items
                SET download_job_id = ?,
                    download_status = CASE
                        WHEN download_status = 'complete' THEN download_status
                        ELSE 'queued'
                    END,
                    updated_at = ?
                WHERE id = ?
                """,
                (job_id, _now(), media_item_id),
            )
            self.conn.execute(
                "UPDATE jobs SET media_item_id = ? WHERE id = ?",
                (media_item_id, job_id),
            )

    def sync_media_for_job(self, job_id: int) -> None:
        """Copy the latest linked download job state onto its media item."""
        job = self.get_job(job_id)
        if job.media_item_id is None:
            return
        self.conn.execute(
            """
            UPDATE media_items
            SET download_status = ?, output_path = ?, last_error = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                job.status,
                job.output_path,
                job.last_error,
                _now(),
                job.media_item_id,
            ),
        )
        self.conn.commit()

    def active_playlist_items(self, playlist_id: int) -> list[PlaylistItem]:
        """Return all active items for scheduling child downloads."""
        rows = self.conn.execute(
            """
            SELECT media_items.*, playlist_memberships.playlist_id,
                   playlist_memberships.position, playlist_memberships.active
            FROM playlist_memberships
            JOIN media_items ON media_items.id = playlist_memberships.media_item_id
            WHERE playlist_memberships.playlist_id = ?
              AND playlist_memberships.active = 1
            ORDER BY playlist_memberships.position ASC
            """,
            (playlist_id,),
        ).fetchall()
        return [_row_to_playlist_item(row) for row in rows]

    def enqueue_work(self, job_id: int) -> bool:
        """Persist a background request unless the job already has active work."""
        try:
            self.conn.execute(
                "INSERT INTO work_items (job_id, status, created_at) VALUES (?, 'pending', ?)",
                (job_id, _now()),
            )
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def recover_work(self) -> int:
        """Return interrupted work to pending state after service restart."""
        with self.conn:
            self.conn.execute(
                """
                UPDATE jobs
                SET status = 'retry',
                    last_error = COALESCE(last_error, 'interrupted by service restart')
                WHERE status = 'running'
                  AND id IN (
                      SELECT job_id FROM work_items WHERE status = 'running'
                  )
                """
            )
            cur = self.conn.execute(
                """
                UPDATE work_items
                SET status = 'pending', started_at = NULL
                WHERE status = 'running'
                """
            )
        return cur.rowcount

    def claim_work(self) -> WorkItem | None:
        """Atomically claim the oldest pending work item."""
        with self.conn:
            row = self.conn.execute(
                "SELECT * FROM work_items WHERE status = 'pending' ORDER BY id ASC LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            cur = self.conn.execute(
                """
                UPDATE work_items
                SET status = 'running', started_at = ?
                WHERE id = ? AND status = 'pending'
                """,
                (_now(), row["id"]),
            )
            if cur.rowcount != 1:
                return None
        return WorkItem(
            id=row["id"],
            job_id=row["job_id"],
            status="running",
            created_at=row["created_at"],
        )

    def finish_work(self, work_id: int, error: str | None = None) -> None:
        """Mark one background request complete or failed."""
        self.conn.execute(
            """
            UPDATE work_items
            SET status = ?, finished_at = ?, last_error = ?
            WHERE id = ?
            """,
            ("failed" if error else "complete", _now(), error, work_id),
        )
        self.conn.commit()


def _row_to_job(row: sqlite3.Row) -> Job:
    """Convert a SQLite row into a Job dataclass."""
    return Job(
        id=row["id"],
        source=Source(row["source"]),
        raw_url=row["raw_url"],
        normalized_url=row["normalized_url"],
        status=row["status"],
        attempts=row["attempts"],
        last_error=row["last_error"],
        last_warning=row["last_warning"],
        output_path=row["output_path"],
        dedupe_key=row["dedupe_key"],
        duplicate_of=row["duplicate_of"],
        allow_duplicate=bool(row["allow_duplicate"]),
        parent_id=row["parent_id"],
        child_count=row["child_count"],
        child_created_count=row["child_created_count"],
        child_duplicate_count=row["child_duplicate_count"],
        child_error_count=row["child_error_count"],
        playlist_id=row["playlist_id"],
        media_item_id=row["media_item_id"],
        job_kind=row["job_kind"],
    )


def _row_to_playlist(row: sqlite3.Row) -> Playlist:
    """Convert a SQLite row into a Playlist dataclass."""
    return Playlist(
        id=row["id"],
        source=Source(row["source"]),
        raw_url=row["raw_url"],
        normalized_url=row["normalized_url"],
        title=row["title"],
        job_id=row["job_id"],
        tracked=bool(row["tracked"]),
        paused=bool(row["paused"]),
        interval_seconds=row["interval_seconds"],
        last_checked_at=row["last_checked_at"],
        last_success_at=row["last_success_at"],
        next_check_at=row["next_check_at"],
        last_error=row["last_error"],
    )


def _row_to_playlist_item(row: sqlite3.Row) -> PlaylistItem:
    """Convert a joined membership/media row into a PlaylistItem."""
    return PlaylistItem(
        id=row["id"],
        playlist_id=row["playlist_id"],
        source=Source(row["source"]),
        provider_id=row["provider_id"],
        url=row["normalized_url"],
        title=row["title"],
        artist=row["artist"],
        position=row["position"],
        active=bool(row["active"]),
        download_status=row["download_status"],
        output_path=row["output_path"],
        last_error=row["last_error"],
        download_job_id=row["download_job_id"],
    )


def _now() -> str:
    """Return the current UTC timestamp in SQLite-friendly ISO format."""
    return datetime.now(UTC).isoformat(timespec="seconds")
