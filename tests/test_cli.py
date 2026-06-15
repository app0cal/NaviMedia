"""Tests for the container-internal CLI argument handling and safety checks."""

from media_dl.cli import main
from media_dl.config import Config
from media_dl.jobs import add_url, list_recent_jobs


def config(tmp_path):
    """Build a test config rooted in a temporary directory."""
    return Config(
        music_root=tmp_path / "music",
        state_dir=tmp_path / "state",
        queue_dir=tmp_path / "queue",
        download_dir=tmp_path / "downloads",
        yt_dlp_bin="false",
        spotdl_bin="false",
    )


def test_clear_history_requires_yes(tmp_path, monkeypatch, capsys):
    """Verify clear-history refuses to run without explicit confirmation."""
    cfg = config(tmp_path)
    add_url(cfg, "https://youtube.com/watch?v=abc", queue_only=True)
    monkeypatch.setattr("media_dl.cli.load_config", lambda: cfg)

    exit_code = main(["clear-history"])

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "without --yes" in captured.err
    assert list_recent_jobs(cfg).total == 1


def test_clear_history_with_yes_clears_jobs_and_archive(tmp_path, monkeypatch, capsys):
    """Verify confirmed clear-history removes jobs and the YouTube archive."""
    cfg = config(tmp_path)
    add_url(cfg, "https://youtube.com/watch?v=abc", queue_only=True)
    cfg.ensure_dirs()
    cfg.yt_archive_path.write_text("youtube abc\n", encoding="utf-8")
    monkeypatch.setattr("media_dl.cli.load_config", lambda: cfg)

    exit_code = main(["clear-history", "--yes"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "cleared 1 job(s); yt-dlp archive removed" in captured.out
    assert not cfg.yt_archive_path.exists()
