from __future__ import annotations

from dataclasses import dataclass

from media_dl.config import Config
from media_dl.db import Database, Job
from media_dl.queue import import_queue
from media_dl.urltools import Source, classify_url
from media_dl.worker import process_job, run_once


class UnsupportedUrlError(ValueError):
    def __init__(self, url: str):
        super().__init__(f"unsupported url: {url}")
        self.url = url


@dataclass(frozen=True)
class AddJobResult:
    job: Job
    created: bool
    processed: Job | None


@dataclass(frozen=True)
class JobActionResult:
    job: Job
    processed: Job | None


@dataclass(frozen=True)
class JobListResult:
    jobs: list[Job]
    limit: int
    offset: int
    total: int

    @property
    def has_more(self) -> bool:
        return self.offset + len(self.jobs) < self.total


def add_url(
    config: Config,
    url: str,
    queue_only: bool = False,
    allow_duplicate: bool = False,
) -> AddJobResult:
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
    config.ensure_dirs()
    db = Database(config.db_path)
    try:
        return db.skip(job_id, reason)
    finally:
        db.close()


def list_recent_jobs(config: Config, limit: int = 25, offset: int = 0) -> JobListResult:
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
    config.ensure_dirs()
    db = Database(config.db_path)
    try:
        return import_queue(config.queue_dir, db)
    finally:
        db.close()


def run_queued_jobs(config: Config, max_jobs: int | None = None) -> int:
    return run_once(config, max_jobs=max_jobs)
