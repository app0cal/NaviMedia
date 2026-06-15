"""Tests for environment-backed Config loading."""

from media_dl.config import load_config


def test_load_config_uses_config_dir_and_default_thumbnail_env(tmp_path, monkeypatch):
    """Verify config and default thumbnail paths come from environment variables."""
    config_dir = tmp_path / "appdata" / "NaviMedia" / "config"
    default_thumbnail = config_dir / "default.jpg"
    monkeypatch.setenv("CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("DEFAULT_THUMBNAIL_PATH", str(default_thumbnail))

    cfg = load_config()

    assert cfg.resolved_config_dir == config_dir.resolve()
    assert cfg.resolved_default_thumbnail_path == default_thumbnail.resolve()
