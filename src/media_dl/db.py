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
    id: int
    source: Source
    raw_url: str
    normalized_url: str
    status: str
    attempts: int
    last_error: str | None
    output_path: str | None
    dedupe_key: str
    duplicate_of: int | None
    allow_duplicate: bool


class Database:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.migrate()

    def close(self) -> None:
        self.conn.close()

    def migrate(self) -> None:
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
        self._create_indexes()
        self.conn.commit()

    def _create_schema(self) -> None:
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
                output_path TEXT
            );
            """
        )
        self._create_indexes()
        self.conn.commit()

    def _create_indexes(self) -> None:
        self.conn.executescript(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_dedupe_key ON jobs(dedupe_key);
            CREATE INDEX IF NOT EXISTS idx_jobs_normalized_url ON jobs(normalized_url);
            CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
            CREATE INDEX IF NOT EXISTS idx_jobs_source ON jobs(source);
            """
        )

    def _rebuild_jobs_table(self, columns: set[str]) -> None:
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
                output_path TEXT
            );
            """
        )

        has_dedupe = "dedupe_key" in columns
        has_duplicate_of = "duplicate_of" in columns
        has_allow_duplicate = "allow_duplicate" in columns
        dedupe_expr = "dedupe_key" if has_dedupe else "normalized_url"
        duplicate_expr = "duplicate_of" if has_duplicate_of else "NULL"
        allow_expr = "allow_duplicate" if has_allow_duplicate else "0"

        self.conn.execute(
            f"""
            INSERT INTO jobs_new (
                id, source, raw_url, normalized_url, dedupe_key, duplicate_of,
                allow_duplicate, status, attempts, first_seen, last_seen,
                started_at, finished_at, last_error, output_path
            )
            SELECT
                id, source, raw_url, normalized_url, {dedupe_expr}, {duplicate_expr},
                {allow_expr}, status, attempts, first_seen, last_seen,
                started_at, finished_at, last_error, output_path
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
    ) -> tuple[Job, bool]:
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
                    allow_duplicate, status, first_seen, last_seen
                )
                VALUES (?, ?, ?, ?, ?, ?, 'queued', ?, ?)
                """,
                (
                    source.value,
                    raw_url,
                    normalized_url,
                    dedupe_key,
                    duplicate_of,
                    1 if allow_duplicate else 0,
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
        row = self.conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(f"job {job_id} not found")
        return _row_to_job(row)

    def get_by_normalized_url(self, normalized_url: str) -> Job:
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
        row = self.conn.execute(
            "SELECT * FROM jobs WHERE dedupe_key = ?",
            (dedupe_key,),
        ).fetchone()
        if row is None:
            raise KeyError(f"dedupe key {dedupe_key} not found")
        return _row_to_job(row)

    def _first_job_id_for_url(self, normalized_url: str) -> int | None:
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

    def list_jobs(self, statuses: Iterable[str] | None = None, limit: int = 50) -> list[Job]:
        if statuses:
            status_list = list(statuses)
            placeholders = ",".join("?" for _ in status_list)
            rows = self.conn.execute(
                f"SELECT * FROM jobs WHERE status IN ({placeholders}) ORDER BY id DESC LIMIT ?",
                (*status_list, limit),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM jobs ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [_row_to_job(row) for row in rows]

    def next_queued(self) -> Job | None:
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
        self.conn.execute(
            """
            UPDATE jobs
            SET status = 'running',
                attempts = attempts + 1,
                started_at = ?,
                last_error = NULL
            WHERE id = ?
            """,
            (_now(), job_id),
        )
        self.conn.commit()

    def mark_complete(self, job_id: int, output_path: str) -> None:
        self.conn.execute(
            """
            UPDATE jobs
            SET status = 'complete',
                finished_at = ?,
                output_path = ?,
                last_error = NULL
            WHERE id = ?
            """,
            (_now(), output_path, job_id),
        )
        self.conn.commit()

    def mark_failed(self, job_id: int, error: str) -> None:
        self.conn.execute(
            """
            UPDATE jobs
            SET status = 'failed',
                finished_at = ?,
                last_error = ?
            WHERE id = ?
            """,
            (_now(), error[-4000:], job_id),
        )
        self.conn.commit()

    def retry(self, job_id: int) -> Job:
        self.conn.execute(
            "UPDATE jobs SET status = 'retry', last_error = NULL WHERE id = ?",
            (job_id,),
        )
        self.conn.commit()
        return self.get_job(job_id)

    def skip(self, job_id: int, reason: str | None = None) -> Job:
        self.conn.execute(
            """
            UPDATE jobs
            SET status = 'skipped',
                finished_at = ?,
                last_error = ?
            WHERE id = ?
            """,
            (_now(), reason, job_id),
        )
        self.conn.commit()
        return self.get_job(job_id)


def _row_to_job(row: sqlite3.Row) -> Job:
    return Job(
        id=row["id"],
        source=Source(row["source"]),
        raw_url=row["raw_url"],
        normalized_url=row["normalized_url"],
        status=row["status"],
        attempts=row["attempts"],
        last_error=row["last_error"],
        output_path=row["output_path"],
        dedupe_key=row["dedupe_key"],
        duplicate_of=row["duplicate_of"],
        allow_duplicate=bool(row["allow_duplicate"]),
    )


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
