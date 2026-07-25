"""Run persistent service work, recurring playlist checks, and event notifications."""

from __future__ import annotations

from threading import Event, Thread

from media_dl.config import Config
from media_dl.db import Database, Playlist
from media_dl.events import EventBroker
from media_dl.worker import process_job


class ServiceRuntime:
    """Own the service's single worker thread and due-playlist scheduler."""

    def __init__(self, config: Config, broker: EventBroker):
        self.config = config
        self.broker = broker
        self._stop = Event()
        self._wake = Event()
        self._thread: Thread | None = None

    def start(self) -> None:
        """Recover persistent work and start the worker once."""
        if self._thread is not None and self._thread.is_alive():
            return
        self.config.ensure_dirs()
        db = Database(self.config.db_path)
        try:
            db.recover_work()
        finally:
            db.close()
        self._stop.clear()
        self._thread = Thread(target=self._run, name="media-dl-worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop the worker and wait briefly for a clean service shutdown."""
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._thread = None

    def wake(self) -> None:
        """Wake the scheduler after tracking settings change."""
        self._wake.set()

    def enqueue_job(self, job_id: int) -> bool:
        """Persist and wake work for one specific job."""
        db = Database(self.config.db_path)
        try:
            queued = db.enqueue_work(job_id)
        finally:
            db.close()
        if queued:
            self.broker.publish("job", job_id=job_id)
            self._wake.set()
        return queued

    def enqueue_queued_jobs(self, max_jobs: int | None) -> int:
        """Queue a bounded number of staged jobs for background execution."""
        db = Database(self.config.db_path)
        queued = 0
        try:
            jobs = db.list_jobs(statuses=["queued", "retry"], limit=100000)
            for job in reversed(jobs):
                if max_jobs is not None and queued >= max_jobs:
                    break
                if db.enqueue_work(job.id):
                    queued += 1
        finally:
            db.close()
        if queued:
            self.broker.publish("jobs")
            self._wake.set()
        return queued

    def queue_playlist_check(self, playlist_id: int) -> tuple[int, bool]:
        """Prepare and queue the stable parent job for one playlist."""
        db = Database(self.config.db_path)
        try:
            playlist = db.get_playlist(playlist_id)
            job_id = self._prepare_playlist_job(db, playlist)
            accepted = db.enqueue_work(job_id)
        finally:
            db.close()
        self.broker.publish("playlist", playlist_id=playlist_id)
        self.broker.publish("job", job_id=job_id)
        self._wake.set()
        return job_id, accepted

    def _run(self) -> None:
        """Process due schedules and persistent work until shutdown."""
        while not self._stop.is_set():
            db = Database(self.config.db_path)
            try:
                for playlist in db.due_playlists():
                    job_id = self._prepare_playlist_job(db, playlist)
                    db.enqueue_work(job_id)
                work = db.claim_work()
            finally:
                db.close()

            if work is None:
                self._wake.wait(timeout=1)
                self._wake.clear()
                continue

            error: str | None = None
            try:
                processed = process_job(self.config, work.job_id)
                error = processed.last_error if processed.status == "failed" else None
            except Exception as exc:
                error = str(exc)

            db = Database(self.config.db_path)
            try:
                db.finish_work(work.id, error=error)
                playlist = db.get_playlist_for_job(work.job_id)
            finally:
                db.close()
            self.broker.publish("job", job_id=work.job_id)
            if playlist is not None:
                self.broker.publish("playlist", playlist_id=playlist.id)
                self.broker.publish("playlist-items", playlist_id=playlist.id)

    @staticmethod
    def _prepare_playlist_job(db: Database, playlist: Playlist) -> int:
        """Create or reset the parent job used for the next playlist check."""
        job = None
        if playlist.job_id is not None:
            try:
                job = db.get_job(playlist.job_id)
            except KeyError:
                job = None
        if job is None:
            existing = None
            try:
                existing = db.get_by_normalized_url(playlist.normalized_url)
            except KeyError:
                pass
            if existing is None:
                job, _ = db.add_job(
                    playlist.source,
                    playlist.raw_url,
                    playlist.normalized_url,
                    playlist_id=playlist.id,
                    job_kind="playlist",
                )
            else:
                job = existing
            db.attach_playlist_job(playlist.id, job.id)
        if job.status not in {"queued", "retry", "running"}:
            job = db.retry(job.id)
        return job.id
