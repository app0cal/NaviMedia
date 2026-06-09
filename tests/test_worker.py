from media_dl.config import Config
from media_dl.db import Database
from media_dl.downloader import DownloadResult
from media_dl.urltools import Source
from media_dl.worker import process_job
from media_dl.runtime_settings import save_runtime_settings


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


def test_process_job_uses_persisted_audio_format(tmp_path, monkeypatch):
    cfg = Config(
        music_root=tmp_path / "music",
        state_dir=tmp_path / "state",
        queue_dir=tmp_path / "queue",
        download_dir=tmp_path / "downloads",
        yt_dlp_bin="yt-dlp",
        spotdl_bin="spotdl",
    )
    save_runtime_settings(cfg, "opus")
    db = Database(cfg.db_path)
    job, _ = db.add_job(Source.YOUTUBE, "raw", "normalized")
    db.close()

    seen = {}

    def fake_run(self, job):
        seen["audio_format"] = self.config.audio_format
        return DownloadResult(output_path=cfg.music_root / "YouTube")

    monkeypatch.setattr("media_dl.downloader.Downloader.run", fake_run)

    processed = process_job(cfg, job.id)

    assert seen["audio_format"] == "opus"
    assert processed.status == "complete"


def test_process_job_uses_persisted_thumbnail_mode(tmp_path, monkeypatch):
    cfg = Config(
        music_root=tmp_path / "music",
        state_dir=tmp_path / "state",
        queue_dir=tmp_path / "queue",
        download_dir=tmp_path / "downloads",
    )
    save_runtime_settings(cfg, "m4a", thumbnail_mode="none")
    db = Database(cfg.db_path)
    job, _ = db.add_job(Source.YOUTUBE, "raw", "normalized")
    db.close()

    seen = {}

    def fake_run(self, job):
        seen["thumbnail_mode"] = self.config.thumbnail_mode
        return DownloadResult(output_path=cfg.music_root / "YouTube")

    monkeypatch.setattr("media_dl.downloader.Downloader.run", fake_run)

    processed = process_job(cfg, job.id)

    assert seen["thumbnail_mode"] == "none"
    assert processed.status == "complete"


def test_process_job_uses_persisted_output_layout(tmp_path, monkeypatch):
    cfg = Config(
        music_root=tmp_path / "music",
        state_dir=tmp_path / "state",
        queue_dir=tmp_path / "queue",
        download_dir=tmp_path / "downloads",
    )
    save_runtime_settings(cfg, "m4a", output_layout="creator_folders")
    db = Database(cfg.db_path)
    job, _ = db.add_job(Source.YOUTUBE, "raw", "normalized")
    db.close()

    seen = {}

    def fake_run(self, job):
        seen["output_layout"] = self.settings.output_layout
        return DownloadResult(output_path=cfg.music_root)

    monkeypatch.setattr("media_dl.downloader.Downloader.run", fake_run)

    processed = process_job(cfg, job.id)

    assert seen["output_layout"] == "creator_folders"
    assert processed.status == "complete"


def test_process_job_persists_success_warning(tmp_path, monkeypatch):
    cfg = Config(
        music_root=tmp_path / "music",
        state_dir=tmp_path / "state",
        queue_dir=tmp_path / "queue",
        download_dir=tmp_path / "downloads",
    )
    db = Database(cfg.db_path)
    job, _ = db.add_job(Source.YOUTUBE, "raw", "normalized")
    db.close()

    def fake_run(self, job):
        return DownloadResult(
            output_path=cfg.music_root / "YouTube",
            warning="Some playlist items failed.",
        )

    monkeypatch.setattr("media_dl.downloader.Downloader.run", fake_run)

    processed = process_job(cfg, job.id)

    assert processed.status == "complete"
    assert processed.last_warning == "Some playlist items failed."
    assert processed.last_error is None
