from __future__ import annotations

import fcntl
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from threading import Lock

from media_dl.config import Config
from media_dl.db import Database
from media_dl.downloader import Downloader
from media_dl.queue import import_queue
from media_dl.runtime_settings import load_runtime_settings


_download_lock = Lock()


def process_job(config: Config, job_id: int):
    config.ensure_dirs()
    db = Database(config.db_path)
    settings = load_runtime_settings(config)
    runtime_config = replace(
        config,
        audio_format=settings.audio_format,
        thumbnail_mode=settings.thumbnail_mode,
    )
    downloader = Downloader(runtime_config, settings=settings)

    try:
        with _exclusive_download(config):
            return _process_job_with_db(db, downloader, job_id)
    finally:
        db.close()


def run_once(config: Config, max_jobs: int | None = None) -> int:
    config.ensure_dirs()
    db = Database(config.db_path)
    settings = load_runtime_settings(config)
    runtime_config = replace(
        config,
        audio_format=settings.audio_format,
        thumbnail_mode=settings.thumbnail_mode,
    )
    downloader = Downloader(runtime_config, settings=settings)
    processed = 0

    try:
        import_queue(config.queue_dir, db)
        while max_jobs is None or processed < max_jobs:
            job = db.next_queued()
            if job is None:
                break

            with _exclusive_download(config):
                _process_job_with_db(db, downloader, job.id)
            processed += 1
    finally:
        db.close()

    return processed


def watch(config: Config) -> None:
    config.ensure_dirs()
    while True:
        processed = run_once(config)
        print(f"processed {processed} job(s)", flush=True)
        time.sleep(config.poll_seconds)


def _process_job_with_db(db: Database, downloader: Downloader, job_id: int):
    job = db.get_job(job_id)
    if job.status not in {"queued", "retry"}:
        return job

    db.mark_running(job.id)
    try:
        result = downloader.run(job)
    except Exception as exc:
        db.mark_failed(job.id, str(exc))
    else:
        db.mark_complete(job.id, str(result.output_path), warning=result.warning)
    return db.get_job(job.id)


@contextmanager
def _exclusive_download(config: Config) -> Iterator[None]:
    lock_path = config.state_dir / "media-dl.lock"
    with _download_lock:
        with lock_path.open("w", encoding="utf-8") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
