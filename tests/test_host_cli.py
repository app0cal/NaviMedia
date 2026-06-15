"""Tests for the host-side CLI wrapper and slash-command shell dispatch."""

from __future__ import annotations

import importlib.util
import sys
from importlib.machinery import SourceFileLoader
from pathlib import Path


def load_host_cli():
    """Load the executable host CLI script as a test module."""
    path = Path(__file__).resolve().parents[1] / "bin" / "media-dl"
    loader = SourceFileLoader("host_media_dl", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class FakeClient:
    """Record API calls and return representative service responses."""

    def __init__(self):
        """Initialize fake API state."""
        self.saved_settings = None
        self.cleared = False

    def settings(self):
        """Return current settings and allowed options."""
        return {
            "audio_format": "m4a",
            "audio_formats": ["m4a", "mp3", "flac", "opus", "wav"],
            "thumbnail_mode": "source",
            "thumbnail_modes": ["source", "default", "none"],
            "output_layout": "navidrome",
            "output_layouts": ["navidrome", "source"],
            "metadata_mode": "navidrome_clean",
            "metadata_modes": ["source", "navidrome_clean"],
            "playlist_mode": "single_job",
            "playlist_modes": ["single_job", "expand_items"],
            "default_thumbnail_path": "/config/default.jpg",
        }

    def save_settings(self, settings):
        """Capture settings updates."""
        self.saved_settings = settings
        return {
            **self.settings(),
            **settings,
        }

    def jobs(self, limit):
        """Return one compact job list."""
        return {
            "total": 1,
            "jobs": [
                {
                    "id": 7,
                    "source": "youtube",
                    "status": "complete",
                    "attempts": 1,
                    "parent_id": None,
                    "child_count": 0,
                    "child_created_count": 0,
                    "last_warning": None,
                    "last_error": None,
                    "normalized_url": "https://youtu.be/abc",
                }
            ],
        }

    def add_job(self, url, queue_only, allow_duplicate):
        """Return an add-job response."""
        return {
            "created": True,
            "job": {
                "id": 2,
                "source": "youtube",
                "normalized_url": url,
                "allow_duplicate": allow_duplicate,
            },
            "processed": None,
        }

    def run_jobs(self, max_jobs):
        """Return a manual run response."""
        return {"processed": max_jobs or 1}

    def import_queue(self):
        """Return an import summary."""
        return {"imported": 1, "duplicates": 2, "errors": 0}

    def retry(self, job_id, queue_only):
        """Return a retry response."""
        return {"job": {"id": job_id}, "processed": None}

    def skip(self, job_id, reason):
        """Return a skip response."""
        return {"job": {"id": job_id}}

    def clear_history(self):
        """Return a clear-history response."""
        self.cleared = True
        return {"deleted_jobs": 3, "archive_deleted": True}


def context(module, tmp_path):
    """Build a host CLI context for tests."""
    return module.Context(repo_root=tmp_path, base_url="http://127.0.0.1:8765")


def test_settings_output_lists_current_values_and_options(tmp_path, monkeypatch, capsys):
    """Verify settings output uses friendly labels and option lists."""
    module = load_host_cli()
    fake = FakeClient()
    monkeypatch.setattr(module, "api_client_or_start", lambda _ctx: fake)

    exit_code = module.main(["--url", "http://example.test", "settings"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Audio Format: {m4a} options: mp3, flac, opus, wav" in captured.out
    assert "Playlist Handling: {single_job} options: expand_items" in captured.out


def test_set_format_alias_merges_existing_settings(tmp_path, monkeypatch, capsys):
    """Verify `set format` updates audio_format while preserving other settings."""
    module = load_host_cli()
    fake = FakeClient()
    monkeypatch.setattr(module, "api_client_or_start", lambda _ctx: fake)

    exit_code = module.main(["set", "format", "opus"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Audio Format set to opus" in captured.out
    assert fake.saved_settings == {
        "audio_format": "opus",
        "thumbnail_mode": "source",
        "output_layout": "navidrome",
        "metadata_mode": "navidrome_clean",
        "playlist_mode": "single_job",
    }


def test_status_prints_recent_table(tmp_path, monkeypatch, capsys):
    """Verify status renders the compact table."""
    module = load_host_cli()
    fake = FakeClient()
    monkeypatch.setattr(module, "api_client_or_start", lambda _ctx: fake)

    exit_code = module.main(["status"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "1 total job(s), showing 1" in captured.out
    assert "youtube complete" in captured.out


def test_add_command_posts_url_options(tmp_path, monkeypatch, capsys):
    """Verify add forwards URL options to the API client."""
    module = load_host_cli()
    fake = FakeClient()
    monkeypatch.setattr(module, "api_client_or_start", lambda _ctx: fake)

    exit_code = module.main(["add", "--queue-only", "--allow-duplicate", "https://youtu.be/abc"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "queued duplicate: job 2 youtube https://youtu.be/abc" in captured.out


def test_clear_history_requires_force(tmp_path, monkeypatch, capsys):
    """Verify destructive one-shot clear-history requires -f."""
    module = load_host_cli()
    fake = FakeClient()
    monkeypatch.setattr(module, "api_client_or_start", lambda _ctx: fake)

    exit_code = module.main(["clear-history"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "without -f" in captured.err
    assert not fake.cleared


def test_clear_history_force_calls_api(tmp_path, monkeypatch, capsys):
    """Verify -f permits one-shot clear-history."""
    module = load_host_cli()
    fake = FakeClient()
    monkeypatch.setattr(module, "api_client_or_start", lambda _ctx: fake)

    exit_code = module.main(["clear-history", "-f"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "cleared 3 job(s); yt-dlp archive removed" in captured.out
    assert fake.cleared


def test_shell_set_dispatches_to_api(tmp_path, monkeypatch, capsys):
    """Verify slash `/set` uses the same settings update path."""
    module = load_host_cli()
    fake = FakeClient()
    monkeypatch.setattr(module, "api_client_or_start", lambda _ctx: fake)

    should_exit = module.dispatch_shell_line(context(module, tmp_path), "/set format opus")

    captured = capsys.readouterr()
    assert not should_exit
    assert "Audio Format set to opus" in captured.out
    assert fake.saved_settings["audio_format"] == "opus"


def test_shell_clear_history_prompts(tmp_path, monkeypatch, capsys):
    """Verify slash clear-history prompts before calling the API."""
    module = load_host_cli()
    fake = FakeClient()
    monkeypatch.setattr(module, "api_client_or_start", lambda _ctx: fake)
    monkeypatch.setattr("builtins.input", lambda _prompt: "y")

    should_exit = module.dispatch_shell_line(context(module, tmp_path), "/clear-history")

    captured = capsys.readouterr()
    assert not should_exit
    assert "cleared 3 job(s); yt-dlp archive removed" in captured.out
    assert fake.cleared


def test_service_down_offer_start_runs_compose_and_retries(tmp_path, monkeypatch):
    """Verify service-down behavior offers startup and waits for health."""
    module = load_host_cli()
    calls = []

    class FlakyClient:
        """Fail the first health check and pass the second."""

        attempts = 0

        def __init__(self, _base_url):
            """Store no state."""

        def health(self):
            """Raise once, then return ok."""
            FlakyClient.attempts += 1
            if FlakyClient.attempts == 1:
                raise ConnectionError("refused")
            return {"status": "ok"}

    monkeypatch.setattr(module, "ApiClient", FlakyClient)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt: "y")
    monkeypatch.setattr(module, "compose", lambda _ctx, args, **_kwargs: calls.append(args))

    client = module.api_client_or_start(context(module, tmp_path))

    assert isinstance(client, FlakyClient)
    assert calls == [["up", "-d"]]
