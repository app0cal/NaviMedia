"""Tests for the service-owned persistent background runtime."""

import time

from media_dl.config import Config
from media_dl.db import Database
from media_dl.events import EventBroker
from media_dl.service_runtime import ServiceRuntime
from media_dl.urltools import Source


def config(tmp_path):
    """Build a runtime config rooted in temporary storage."""
    return Config(
        music_root=tmp_path / "music",
        state_dir=tmp_path / "state",
        queue_dir=tmp_path / "queue",
        download_dir=tmp_path / "downloads",
    )


def test_runtime_processes_persistent_work(tmp_path, monkeypatch):
    """Verify the service thread claims and completes queued work."""
    cfg = config(tmp_path)
    db = Database(cfg.db_path)
    job, _ = db.add_job(Source.YOUTUBE, "raw", "normalized")
    db.close()

    def fake_process(config, job_id):
        worker_db = Database(config.db_path)
        worker_db.mark_running(job_id)
        worker_db.mark_complete(job_id, "output")
        processed = worker_db.get_job(job_id)
        worker_db.close()
        return processed

    monkeypatch.setattr("media_dl.service_runtime.process_job", fake_process)
    runtime = ServiceRuntime(cfg, EventBroker())
    runtime.start()
    try:
        assert runtime.enqueue_job(job.id) is True
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            check_db = Database(cfg.db_path)
            status = check_db.get_job(job.id).status
            check_db.close()
            if status == "complete":
                break
            time.sleep(0.02)
        assert status == "complete"
    finally:
        runtime.stop()
