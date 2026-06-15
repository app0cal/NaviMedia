"""Process queued jobs with runtime settings, locking, and playlist expansion."""

from __future__ import annotations

import fcntl
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from threading import Lock

from media_dl.config import Config
from media_dl.db import Database
from media_dl.download_plan import PLAYLIST_MODE_EXPAND_ITEMS
from media_dl.downloader import Downloader
from media_dl.playlist import extract_youtube_playlist_items, is_youtube_playlist_url
from media_dl.queue import import_queue
from media_dl.runtime_settings import load_runtime_settings
from media_dl.urltools import Source, classify_url


_download_lock = Lock()


def process_job(config: Config, job_id: int):
    """Process one specific queued or retry job under the shared download lock."""
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
    """Import queue files and process up to max_jobs queued jobs."""
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
    """Continuously run the queue processor for legacy polling deployments."""
    config.ensure_dirs()
    while True:
        processed = run_once(config)
        print(f"processed {processed} job(s)", flush=True)
        time.sleep(config.poll_seconds)


def _process_job_with_db(db: Database, downloader: Downloader, job_id: int):
    """Process one job using an existing database and downloader instance."""
    job = db.get_job(job_id)
    if job.status not in {"queued", "retry"}:
        return job

    db.mark_running(job.id)
    try:
        if _should_expand_playlist(downloader, job):
            _expand_playlist_job(db, downloader, job)
        else:
            result = downloader.run(job)
            db.mark_complete(job.id, str(result.output_path), warning=result.warning)
    except Exception as exc:
        db.mark_failed(job.id, str(exc))
    return db.get_job(job.id)


def _should_expand_playlist(downloader: Downloader, job) -> bool:
    """Return whether a YouTube job should become playlist child jobs."""
    return (
        job.source == Source.YOUTUBE
        and downloader.settings.playlist_mode == PLAYLIST_MODE_EXPAND_ITEMS
        and is_youtube_playlist_url(job.normalized_url)
    )


def _expand_playlist_job(db: Database, downloader: Downloader, job) -> None:
    """Extract playlist children, enqueue them, and complete the parent summary."""
    items = extract_youtube_playlist_items(
        downloader.config.yt_dlp_bin,
        job.normalized_url,
    )
    created_count = 0
    duplicate_count = 0
    error_count = items.error_count

    for url in items.urls:
        classified = classify_url(url)
        if classified.source != Source.YOUTUBE:
            error_count += 1
            continue
        try:
            _child, created = db.add_job(
                classified.source,
                classified.raw_url,
                classified.normalized_url,
                allow_duplicate=job.allow_duplicate,
                parent_id=job.id,
            )
        except Exception:
            error_count += 1
            continue
        if created:
            created_count += 1
        else:
            duplicate_count += 1

    child_count = len(items.urls) + items.error_count
    warning = (
        "Expanded playlist: "
        f"{created_count} queued, {duplicate_count} duplicates, {error_count} errors."
    )
    db.mark_expanded(
        job.id,
        child_count=child_count,
        child_created_count=created_count,
        child_duplicate_count=duplicate_count,
        child_error_count=error_count,
        warning=warning,
    )


@contextmanager
def _exclusive_download(config: Config) -> Iterator[None]:
    """Prevent overlapping downloads across service and one-off containers."""
    lock_path = config.state_dir / "media-dl.lock"
    with _download_lock:
        with lock_path.open("w", encoding="utf-8") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
