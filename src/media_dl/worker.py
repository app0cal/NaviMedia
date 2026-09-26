"""Process queued jobs with runtime settings, locking, and playlist expansion."""

from __future__ import annotations

import fcntl
import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from threading import Lock

from media_dl.config import Config
from media_dl.db import Database
from media_dl.download_plan import PLAYLIST_MODE_EXPAND_ITEMS
from media_dl.downloader import Downloader
from media_dl.playlist import (
    PlaylistEntry,
    PlaylistItems,
    extract_spotify_playlist_items,
    extract_youtube_playlist_items,
    is_playlist_url,
    is_youtube_playlist_url,
)
from media_dl.queue import import_queue
from media_dl.runtime_settings import load_runtime_settings
from media_dl.urltools import Source, classify_url


_download_lock = Lock()
logger = logging.getLogger(__name__)


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
    logger.info("Job %s started: source=%s url=%s", job.id, job.source.value, job.normalized_url)
    try:
        if is_playlist_url(job.normalized_url):
            _process_playlist_job(db, downloader, job)
        else:
            result = downloader.run(job)
            db.mark_complete(job.id, str(result.output_path), warning=result.warning)
    except Exception as exc:
        playlist = db.get_playlist_for_job(job.id)
        if playlist is not None:
            db.mark_playlist_check_failed(playlist.id, str(exc))
        db.mark_failed(job.id, str(exc))
        logger.error("Job %s failed: source=%s\n%s", job.id, job.source.value, exc)
    processed = db.get_job(job.id)
    db.sync_media_for_job(job.id)
    if processed.status != "failed":
        if processed.last_warning:
            logger.warning("Job %s %s: %s", job.id, processed.status, processed.last_warning)
        else:
            logger.info("Job %s %s", job.id, processed.status)
    return processed


def _process_playlist_job(db: Database, downloader: Downloader, job) -> None:
    """Inventory a playlist, then preserve batch or itemized download policy."""
    playlist = db.get_playlist_for_job(job.id) or db.ensure_playlist(job)
    job = db.get_job(job.id)
    try:
        items = _extract_playlist_items(downloader, job)
    except Exception as exc:
        if playlist.tracked or _should_expand_playlist(downloader, job):
            raise
        # A one-time batch download may still succeed when metadata-only inventory
        # is temporarily unavailable.
        raise_inventory_error = str(exc)
        db.mark_playlist_check_failed(playlist.id, raise_inventory_error)
        result = downloader.run(job)
        db.mark_complete(
            job.id,
            str(result.output_path),
            warning=_join_warnings(result.warning, f"Playlist inventory failed: {raise_inventory_error}"),
        )
        return

    entries = items.entries or _placeholder_entries(job.source, items.urls)
    db.reconcile_playlist_items(playlist.id, entries)
    playlist = db.mark_playlist_check_success(playlist.id, title=items.title)
    if playlist.tracked:
        _expand_tracked_playlist_job(db, job, playlist.id, items.error_count)
    elif _should_expand_playlist(downloader, job):
        _expand_playlist_job(db, downloader, job, items=items)
    else:
        result = downloader.run(job)
        warning = result.warning
        if items.error_count:
            warning = _join_warnings(
                warning,
                f"Inventory skipped {items.error_count} unparseable item(s).",
            )
        db.mark_complete(job.id, str(result.output_path), warning=warning)


def _extract_playlist_items(downloader: Downloader, job) -> PlaylistItems:
    """Dispatch metadata-only playlist extraction to the source provider."""
    if job.source == Source.YOUTUBE:
        return extract_youtube_playlist_items(
            downloader.config.yt_dlp_bin,
            job.normalized_url,
        )
    if job.source == Source.SPOTIFY:
        return extract_spotify_playlist_items(
            downloader.config.spotdl_bin,
            job.normalized_url,
        )
    raise ValueError(f"unsupported playlist source: {job.source}")


def _should_expand_playlist(downloader: Downloader, job) -> bool:
    """Return whether a YouTube job should become playlist child jobs."""
    return (
        job.source == Source.YOUTUBE
        and downloader.settings.playlist_mode == PLAYLIST_MODE_EXPAND_ITEMS
        and is_youtube_playlist_url(job.normalized_url)
    )


def _expand_playlist_job(
    db: Database,
    downloader: Downloader,
    job,
    *,
    items: PlaylistItems | None = None,
) -> None:
    """Extract playlist children, enqueue them, and complete the parent summary."""
    items = items or extract_youtube_playlist_items(
        downloader.config.yt_dlp_bin, job.normalized_url
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
                playlist_id=job.playlist_id,
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


def _expand_tracked_playlist_job(
    db: Database,
    job,
    playlist_id: int,
    extraction_errors: int,
) -> None:
    """Create or retry canonical item jobs for one tracked playlist check."""
    created_count = 0
    duplicate_count = 0
    error_count = extraction_errors
    for item in db.active_playlist_items(playlist_id):
        try:
            if item.download_status == "complete":
                duplicate_count += 1
                continue
            classified = classify_url(item.url)
            if classified.source == Source.UNKNOWN:
                error_count += 1
                continue
            child, created = db.add_job(
                classified.source,
                classified.raw_url,
                classified.normalized_url,
                parent_id=job.id,
                playlist_id=playlist_id,
                media_item_id=item.id,
            )
            db.link_media_download_job(item.id, child.id)
            if child.status in {"failed", "skipped"}:
                child = db.retry(child.id)
            if child.status in {"queued", "retry"}:
                db.enqueue_work(child.id)
            if created:
                created_count += 1
            else:
                duplicate_count += 1
        except Exception:
            error_count += 1

    total = len(db.active_playlist_items(playlist_id)) + extraction_errors
    warning = (
        "Tracked playlist checked: "
        f"{created_count} new, {duplicate_count} known, {error_count} errors."
    )
    db.mark_expanded(
        job.id,
        child_count=total,
        child_created_count=created_count,
        child_duplicate_count=duplicate_count,
        child_error_count=error_count,
        warning=warning,
    )


def _placeholder_entries(source: Source, urls: list[str]) -> list[PlaylistEntry]:
    """Build compatibility inventory entries when an extractor only returns URLs."""
    entries: list[PlaylistEntry] = []
    for position, url in enumerate(urls, start=1):
        classified = classify_url(url)
        provider_id = classified.normalized_url.rsplit("/", 1)[-1]
        if "?v=" in classified.normalized_url:
            provider_id = classified.normalized_url.split("?v=", 1)[1].split("&", 1)[0]
        entries.append(
            PlaylistEntry(
                source=source,
                provider_id=provider_id,
                url=classified.normalized_url,
                title="Unknown title",
                artist="Unknown artist",
                position=position,
            )
        )
    return entries


def _join_warnings(*warnings: str | None) -> str | None:
    """Join non-empty warning fragments."""
    present = [warning for warning in warnings if warning]
    return " ".join(present) if present else None


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
