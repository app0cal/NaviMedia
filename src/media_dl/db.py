from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable

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
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS jobs (
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

            CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
            CREATE INDEX IF NOT EXISTS idx_jobs_source ON jobs(source);
            """
        )
        self.conn.commit()

    def add_job(self, source: Source, raw_url: str, normalized_url: str) -> tuple[Job, bool]:
        now = _now()
        try:
            cur = self.conn.execute(
                """
                INSERT INTO jobs (source, raw_url, normalized_url, status, first_seen, last_seen)
                VALUES (?, ?, ?, 'queued', ?, ?)
                """,
                (source.value, raw_url, normalized_url, now, now),
            )
            self.conn.commit()
            return self.get_job(cur.lastrowid), True
        except sqlite3.IntegrityError:
            self.conn.execute(
                "UPDATE jobs SET last_seen = ? WHERE normalized_url = ?",
                (now, normalized_url),
            )
            self.conn.commit()
            return self.get_by_normalized_url(normalized_url), False

    def get_job(self, job_id: int) -> Job:
        row = self.conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(f"job {job_id} not found")
        return _row_to_job(row)

    def get_by_normalized_url(self, normalized_url: str) -> Job:
        row = self.conn.execute(
            "SELECT * FROM jobs WHERE normalized_url = ?",
            (normalized_url,),
        ).fetchone()
        if row is None:
            raise KeyError(f"url {normalized_url} not found")
        return _row_to_job(row)

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
    )


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
