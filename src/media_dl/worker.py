from __future__ import annotations

import time

from media_dl.config import Config
from media_dl.db import Database
from media_dl.downloader import Downloader
from media_dl.queue import import_queue


def process_job(config: Config, job_id: int):
    config.ensure_dirs()
    db = Database(config.db_path)
    downloader = Downloader(config)

    try:
        job = db.get_job(job_id)
        if job.status not in {"queued", "retry"}:
            return job

        db.mark_running(job.id)
        try:
            result = downloader.run(job)
        except Exception as exc:
            db.mark_failed(job.id, str(exc))
        else:
            db.mark_complete(job.id, str(result.output_path))
        return db.get_job(job.id)
    finally:
        db.close()


def run_once(config: Config, max_jobs: int | None = None) -> int:
    config.ensure_dirs()
    db = Database(config.db_path)
    downloader = Downloader(config)
    processed = 0

    try:
        import_queue(config.queue_dir, db)
        while max_jobs is None or processed < max_jobs:
            job = db.next_queued()
            if job is None:
                break

            db.mark_running(job.id)
            try:
                result = downloader.run(job)
            except Exception as exc:
                db.mark_failed(job.id, str(exc))
            else:
                db.mark_complete(job.id, str(result.output_path))
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
