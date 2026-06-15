"""Provide shared job workflows used by the API and container-internal CLI."""

from __future__ import annotations

from dataclasses import dataclass

from media_dl.config import Config
from media_dl.db import Database, Job
from media_dl.queue import import_queue
from media_dl.urltools import Source, classify_url
from media_dl.worker import process_job, run_once


class UnsupportedUrlError(ValueError):
    """Signal that a submitted URL cannot be handled by any source downloader."""

    def __init__(self, url: str):
        """Store the unsupported URL in the error message."""
        super().__init__(f"unsupported url: {url}")
        self.url = url


@dataclass(frozen=True)
class AddJobResult:
    """Return the queued job plus whether it was created and processed."""

    job: Job
    created: bool
    processed: Job | None


@dataclass(frozen=True)
class JobActionResult:
    """Return a changed job plus optional immediate processing result."""

    job: Job
    processed: Job | None


@dataclass(frozen=True)
class JobListResult:
    """Return one paginated job page and total count."""

    jobs: list[Job]
    limit: int
    offset: int
    total: int

    @property
    def has_more(self) -> bool:
        """Return whether another page exists after this result set."""
        return self.offset + len(self.jobs) < self.total


@dataclass(frozen=True)
class ClearHistoryResult:
    """Return counts from clearing job history and the YouTube archive."""

    deleted_jobs: int
    archive_deleted: bool


class ClearHistoryBlockedError(RuntimeError):
    """Signal that cleanup cannot run while a job is actively running."""

    def __init__(self):
        """Use a stable human-facing cleanup rejection message."""
        super().__init__("cannot clear history while a job is running")


def add_url(
    config: Config,
    url: str,
    queue_only: bool = False,
    allow_duplicate: bool = False,
) -> AddJobResult:
    """Classify, dedupe, enqueue, and optionally process a submitted URL."""
    config.ensure_dirs()
    classified = classify_url(url)
    if classified.source == Source.UNKNOWN:
        raise UnsupportedUrlError(url)

    db = Database(config.db_path)
    try:
        job, created = db.add_job(
            classified.source,
            classified.raw_url,
            classified.normalized_url,
            allow_duplicate=allow_duplicate,
        )
    finally:
        db.close()

    processed = None
    if not queue_only and job.status in {"queued", "retry"}:
        processed = process_job(config, job.id)
    return AddJobResult(job=job, created=created, processed=processed)


def retry_job(config: Config, job_id: int, queue_only: bool = False) -> JobActionResult:
    """Queue a job for retry and optionally process it immediately."""
    config.ensure_dirs()
    db = Database(config.db_path)
    try:
        job = db.retry(job_id)
    finally:
        db.close()

    processed = None
    if not queue_only:
        processed = process_job(config, job.id)
    return JobActionResult(job=job, processed=processed)


def skip_job(config: Config, job_id: int, reason: str) -> Job:
    """Mark a job skipped with a user-facing reason."""
    config.ensure_dirs()
    db = Database(config.db_path)
    try:
        return db.skip(job_id, reason)
    finally:
        db.close()


def list_recent_jobs(config: Config, limit: int = 25, offset: int = 0) -> JobListResult:
    """Return a bounded newest-first page of job history."""
    config.ensure_dirs()
    db = Database(config.db_path)
    try:
        return JobListResult(
            jobs=db.list_jobs(limit=limit, offset=offset),
            limit=limit,
            offset=offset,
            total=db.count_jobs(),
        )
    finally:
        db.close()


def import_queue_files(config: Config) -> list[tuple[Job | None, bool, str | None]]:
    """Import standard queue files through the same add-job path."""
    config.ensure_dirs()
    db = Database(config.db_path)
    try:
        return import_queue(config.queue_dir, db)
    finally:
        db.close()


def run_queued_jobs(config: Config, max_jobs: int | None = None) -> int:
    """Process queued jobs using the worker loop."""
    return run_once(config, max_jobs=max_jobs)


def clear_history(config: Config) -> ClearHistoryResult:
    """Clear job history and the yt-dlp archive while preserving music and settings."""
    config.ensure_dirs()
    db = Database(config.db_path)
    try:
        if db.count_jobs(statuses=["running"]) > 0:
            raise ClearHistoryBlockedError()
        deleted_jobs = db.clear_jobs()
    finally:
        db.close()

    archive_deleted = False
    try:
        config.yt_archive_path.unlink()
        archive_deleted = True
    except FileNotFoundError:
        pass

    return ClearHistoryResult(
        deleted_jobs=deleted_jobs,
        archive_deleted=archive_deleted,
    )
