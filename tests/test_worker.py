from media_dl.config import Config
from media_dl.db import Database
from media_dl.urltools import Source
from media_dl.worker import process_job


def test_process_job_ignores_completed_job(tmp_path):
    cfg = Config(
        music_root=tmp_path / "music",
        state_dir=tmp_path / "state",
        queue_dir=tmp_path / "queue",
        download_dir=tmp_path / "downloads",
    )
    db = Database(cfg.db_path)
    job, _ = db.add_job(Source.YOUTUBE, "raw", "normalized")
    db.mark_complete(job.id, "output")
    db.close()

    processed = process_job(cfg, job.id)

    assert processed.status == "complete"
    assert processed.attempts == 0
