"""Manage SQLite job persistence, schema migrations, and job state transitions."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
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


class Database:
    """Wrap SQLite access for jobs while keeping callers on dataclass objects."""

    def __init__(self, path: Path):
        """Open the database file and ensure the current schema exists."""
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
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
                child_error_count INTEGER NOT NULL DEFAULT 0
            );
            """
        )
        self._create_indexes()
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
                child_error_count INTEGER NOT NULL DEFAULT 0
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

        self.conn.execute(
            f"""
            INSERT INTO jobs_new (
                id, source, raw_url, normalized_url, dedupe_key, duplicate_of,
                allow_duplicate, status, attempts, first_seen, last_seen,
                started_at, finished_at, last_error, last_warning, output_path,
                parent_id, child_count, child_created_count, child_duplicate_count,
                child_error_count
            )
            SELECT
                id, source, raw_url, normalized_url, {dedupe_expr}, {duplicate_expr},
                {allow_expr}, status, attempts, first_seen, last_seen,
                started_at, finished_at, last_error, {warning_expr}, output_path,
                {parent_expr}, {child_count_expr}, {child_created_expr},
                {child_duplicate_expr}, {child_error_expr}
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
                    allow_duplicate, parent_id, status, first_seen, last_seen
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?)
                """,
                (
                    source.value,
                    raw_url,
                    normalized_url,
                    dedupe_key,
                    duplicate_of,
                    1 if allow_duplicate else 0,
                    parent_id,
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
                "UPDATE jobs SET last_seen = ? WHERE dedupe_key = ?",
                (now, normalized_url),
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
        """Delete all jobs and reset the jobs autoincrement sequence."""
        deleted = self.count_jobs()
        with self.conn:
            self.conn.execute("DELETE FROM jobs")
            self.conn.execute("DELETE FROM sqlite_sequence WHERE name = 'jobs'")
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
    )


def _now() -> str:
    """Return the current UTC timestamp in SQLite-friendly ISO format."""
    return datetime.now(UTC).isoformat(timespec="seconds")
