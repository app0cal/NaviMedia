"""Tests for FastAPI routes, dashboard serving, settings, and job actions."""

from fastapi.testclient import TestClient

from media_dl.config import Config
from media_dl.db import Database
import media_dl.service as service
from media_dl.service import create_app
from media_dl.urltools import Source


def config(tmp_path):
    """Build a test config rooted in a temporary directory."""
    return Config(
        music_root=tmp_path / "music",
        state_dir=tmp_path / "state",
        queue_dir=tmp_path / "queue",
        download_dir=tmp_path / "downloads",
        service_port=8765,
        yt_dlp_bin="false",
        spotdl_bin="false",
    )


def test_health_endpoint(tmp_path):
    """Verify the health endpoint returns service status."""
    client = TestClient(create_app(config(tmp_path)))

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_runtime_endpoint(tmp_path):
    """Verify runtime endpoint exposes resolved paths and settings."""
    cfg = config(tmp_path)
    client = TestClient(create_app(cfg))

    response = client.get("/api/runtime")

    assert response.status_code == 200
    data = response.json()
    assert data["music_root"] == str(cfg.music_root)
    assert data["state_dir"] == str(cfg.state_dir)
    assert data["queue_dir"] == str(cfg.queue_dir)
    assert data["download_dir"] == str(cfg.download_dir)
    assert data["config_dir"] == str(cfg.resolved_config_dir)
    assert data["default_thumbnail_path"] == str(cfg.resolved_default_thumbnail_path)
    assert "poll_seconds" not in data
    assert data["service_port"] == 8765
    assert data["audio_format"] == "m4a"
    assert data["thumbnail_mode"] == "source"
    assert data["output_layout"] == "artist_album_folders"
    assert data["metadata_mode"] == "navidrome_clean"
    assert data["playlist_mode"] == "single_job"


def test_settings_endpoint_defaults_and_lists_allowed_formats(tmp_path):
    """Verify settings endpoint returns defaults and allowed option lists."""
    client = TestClient(create_app(config(tmp_path)))

    response = client.get("/api/settings")

    assert response.status_code == 200
    assert response.json() == {
        "audio_format": "m4a",
        "audio_formats": ["m4a", "mp3", "flac", "opus", "wav"],
        "thumbnail_mode": "source",
        "thumbnail_modes": ["source", "default", "none"],
        "output_layout": "artist_album_folders",
        "output_layouts": ["source_folders", "creator_folders", "artist_album_folders"],
        "metadata_mode": "navidrome_clean",
        "metadata_modes": ["source", "navidrome_clean"],
        "playlist_mode": "single_job",
        "playlist_modes": ["single_job", "expand_items"],
        "default_thumbnail_path": str(config(tmp_path).resolved_default_thumbnail_path),
    }


def test_settings_endpoint_persists_audio_format(tmp_path):
    """Verify audio and thumbnail setting updates persist."""
    cfg = config(tmp_path)
    client = TestClient(create_app(cfg))

    response = client.put(
        "/api/settings",
        json={"audio_format": "mp3", "thumbnail_mode": "default"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "audio_format": "mp3",
        "audio_formats": ["m4a", "mp3", "flac", "opus", "wav"],
        "thumbnail_mode": "default",
        "thumbnail_modes": ["source", "default", "none"],
        "output_layout": "artist_album_folders",
        "output_layouts": ["source_folders", "creator_folders", "artist_album_folders"],
        "metadata_mode": "navidrome_clean",
        "metadata_modes": ["source", "navidrome_clean"],
        "playlist_mode": "single_job",
        "playlist_modes": ["single_job", "expand_items"],
        "default_thumbnail_path": str(cfg.resolved_default_thumbnail_path),
    }
    assert client.get("/api/runtime").json()["audio_format"] == "mp3"
    assert client.get("/api/runtime").json()["thumbnail_mode"] == "default"
    persisted = TestClient(create_app(cfg)).get("/api/settings").json()
    assert persisted["audio_format"] == "mp3"
    assert persisted["thumbnail_mode"] == "default"


def test_settings_endpoint_persists_download_policy_fields(tmp_path):
    """Verify layout, metadata, and playlist policy settings persist."""
    cfg = config(tmp_path)
    client = TestClient(create_app(cfg))

    response = client.put(
        "/api/settings",
        json={
            "audio_format": "opus",
            "thumbnail_mode": "none",
            "output_layout": "artist_album_folders",
            "metadata_mode": "navidrome_clean",
            "playlist_mode": "expand_items",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["output_layout"] == "artist_album_folders"
    assert data["metadata_mode"] == "navidrome_clean"
    assert data["playlist_mode"] == "expand_items"
    runtime = client.get("/api/runtime").json()
    assert runtime["output_layout"] == "artist_album_folders"
    assert runtime["metadata_mode"] == "navidrome_clean"
    assert runtime["playlist_mode"] == "expand_items"


def test_settings_endpoint_rejects_unsupported_audio_format(tmp_path):
    """Verify invalid audio formats are rejected."""
    client = TestClient(create_app(config(tmp_path)))

    response = client.put("/api/settings", json={"audio_format": "aac"})

    assert response.status_code == 400
    assert response.json() == {"detail": "unsupported audio format: aac"}


def test_settings_endpoint_rejects_unsupported_thumbnail_mode(tmp_path):
    """Verify invalid thumbnail modes are rejected."""
    client = TestClient(create_app(config(tmp_path)))

    response = client.put(
        "/api/settings",
        json={"audio_format": "m4a", "thumbnail_mode": "weird"},
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "unsupported thumbnail mode: weird"}


def test_settings_endpoint_rejects_unsupported_output_layout(tmp_path):
    """Verify invalid output layouts are rejected."""
    client = TestClient(create_app(config(tmp_path)))

    response = client.put(
        "/api/settings",
        json={"audio_format": "m4a", "thumbnail_mode": "source", "output_layout": "messy"},
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "unsupported output layout: messy"}


def test_settings_endpoint_rejects_unsupported_metadata_mode(tmp_path):
    """Verify invalid metadata modes are rejected."""
    client = TestClient(create_app(config(tmp_path)))

    response = client.put(
        "/api/settings",
        json={
            "audio_format": "m4a",
            "thumbnail_mode": "source",
            "metadata_mode": "rewrite_everything",
        },
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "unsupported metadata mode: rewrite_everything"}


def test_web_index_served_when_built(tmp_path, monkeypatch):
    """Verify the built dashboard index is served at root."""
    web_dir = tmp_path / "web-dist"
    web_dir.mkdir()
    (web_dir / "index.html").write_text("<html><body>dashboard</body></html>", encoding="utf-8")
    monkeypatch.setattr(service, "WEB_DIST_DIR", web_dir)
    client = TestClient(create_app(config(tmp_path)))

    response = client.get("/")

    assert response.status_code == 200
    assert "dashboard" in response.text
    assert response.headers["content-type"].startswith("text/html")


def test_web_fallback_serves_dashboard_for_non_api_paths(tmp_path, monkeypatch):
    """Verify non-API routes fall back to the dashboard."""
    web_dir = tmp_path / "web-dist"
    web_dir.mkdir()
    (web_dir / "index.html").write_text("<html><body>dashboard</body></html>", encoding="utf-8")
    monkeypatch.setattr(service, "WEB_DIST_DIR", web_dir)
    client = TestClient(create_app(config(tmp_path)))

    response = client.get("/jobs/123")

    assert response.status_code == 200
    assert "dashboard" in response.text


def test_unknown_api_path_stays_api_404(tmp_path, monkeypatch):
    """Verify unknown API paths return JSON 404 instead of dashboard HTML."""
    web_dir = tmp_path / "web-dist"
    web_dir.mkdir()
    (web_dir / "index.html").write_text("<html><body>dashboard</body></html>", encoding="utf-8")
    monkeypatch.setattr(service, "WEB_DIST_DIR", web_dir)
    client = TestClient(create_app(config(tmp_path)))

    response = client.get("/api/not-found")

    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}


def test_jobs_endpoint_returns_existing_jobs(tmp_path):
    """Verify jobs endpoint returns newest-first jobs with parent fields."""
    client = TestClient(create_app(config(tmp_path)))
    first = client.post(
        "/api/jobs",
        json={"url": "https://youtube.com/watch?v=abc", "queue_only": True},
    ).json()["job"]
    second = client.post(
        "/api/jobs",
        json={"url": "https://youtube.com/watch?v=def", "queue_only": True},
    ).json()["job"]

    response = client.get("/api/jobs")

    assert response.status_code == 200
    data = response.json()
    jobs = data["jobs"]
    assert [job["id"] for job in jobs] == [second["id"], first["id"]]
    assert jobs[0]["source"] == "youtube"
    assert jobs[0]["parent_id"] is None
    assert jobs[0]["child_count"] == 0
    assert jobs[0]["child_created_count"] == 0
    assert jobs[0]["child_duplicate_count"] == 0
    assert jobs[0]["child_error_count"] == 0
    assert data["limit"] == 25
    assert data["offset"] == 0
    assert data["total"] == 2
    assert data["has_more"] is False


def test_jobs_endpoint_paginates_existing_jobs(tmp_path):
    """Verify bounded job pagination over multiple pages."""
    client = TestClient(create_app(config(tmp_path)))
    for index in range(30):
        client.post(
            "/api/jobs",
            json={"url": f"https://youtube.com/watch?v={index}", "queue_only": True},
        )

    first = client.get("/api/jobs")
    second = client.get("/api/jobs?limit=25&offset=25")

    assert first.status_code == 200
    first_data = first.json()
    assert len(first_data["jobs"]) == 25
    assert first_data["jobs"][0]["id"] == 30
    assert first_data["limit"] == 25
    assert first_data["offset"] == 0
    assert first_data["total"] == 30
    assert first_data["has_more"] is True

    assert second.status_code == 200
    second_data = second.json()
    assert [job["id"] for job in second_data["jobs"]] == [5, 4, 3, 2, 1]
    assert second_data["total"] == 30
    assert second_data["has_more"] is False


def test_jobs_endpoint_rejects_unbounded_limit(tmp_path):
    """Verify job listing rejects limits above the API cap."""
    client = TestClient(create_app(config(tmp_path)))

    response = client.get("/api/jobs?limit=101")

    assert response.status_code == 422


def test_create_job_endpoint_queues_background_work(tmp_path):
    """Verify create-job returns promptly after accepting background work."""
    client = TestClient(create_app(config(tmp_path)))

    response = client.post("/api/jobs", json={"url": "https://youtube.com/watch?v=abc"})

    assert response.status_code == 200
    data = response.json()
    assert data["created"] is True
    assert data["job"]["status"] == "queued"
    assert data["processed"] is None
    assert data["accepted"] is True


def test_create_job_endpoint_dedupes_by_default(tmp_path):
    """Verify create-job endpoint dedupes normalized URLs."""
    client = TestClient(create_app(config(tmp_path)))

    first = client.post(
        "/api/jobs",
        json={"url": "https://youtube.com/watch?v=abc", "queue_only": True},
    ).json()
    second = client.post(
        "/api/jobs",
        json={
            "url": "https://youtube.com/watch?v=abc&utm_source=test",
            "queue_only": True,
        },
    ).json()

    assert first["created"] is True
    assert second["created"] is False
    assert second["job"]["id"] == first["job"]["id"]


def test_create_job_endpoint_allows_forced_duplicate(tmp_path):
    """Verify create-job endpoint can create explicit duplicate jobs."""
    client = TestClient(create_app(config(tmp_path)))

    first = client.post(
        "/api/jobs",
        json={"url": "https://youtube.com/watch?v=abc", "queue_only": True},
    ).json()
    second = client.post(
        "/api/jobs",
        json={
            "url": "https://youtube.com/watch?v=abc",
            "queue_only": True,
            "allow_duplicate": True,
        },
    ).json()

    assert second["created"] is True
    assert second["job"]["id"] != first["job"]["id"]
    assert second["job"]["duplicate_of"] == first["job"]["id"]
    assert second["job"]["allow_duplicate"] is True


def test_create_job_endpoint_rejects_unsupported_url(tmp_path):
    """Verify create-job endpoint rejects unknown URL sources."""
    client = TestClient(create_app(config(tmp_path)))

    response = client.post("/api/jobs", json={"url": "https://example.com/item"})

    assert response.status_code == 400
    assert response.json() == {"detail": "unsupported url: https://example.com/item"}


def test_retry_endpoint_marks_retry_and_respects_queue_only(tmp_path):
    """Verify retry endpoint can queue retry without processing."""
    client = TestClient(create_app(config(tmp_path)))
    job = client.post(
        "/api/jobs",
        json={"url": "https://youtube.com/watch?v=abc", "queue_only": True},
    ).json()["job"]
    client.post(f"/api/jobs/{job['id']}/skip", json={"reason": "test"})

    response = client.post(f"/api/jobs/{job['id']}/retry", json={"queue_only": True})

    assert response.status_code == 200
    data = response.json()
    assert data["job"]["status"] == "retry"
    assert data["processed"] is None


def test_retry_endpoint_returns_404_for_unknown_job(tmp_path):
    """Verify retry endpoint returns 404 for absent jobs."""
    client = TestClient(create_app(config(tmp_path)))

    response = client.post("/api/jobs/404/retry", json={"queue_only": True})

    assert response.status_code == 404


def test_skip_endpoint_marks_skipped_with_reason(tmp_path):
    """Verify skip endpoint stores status and reason."""
    client = TestClient(create_app(config(tmp_path)))
    job = client.post(
        "/api/jobs",
        json={"url": "https://youtube.com/watch?v=abc", "queue_only": True},
    ).json()["job"]

    response = client.post(f"/api/jobs/{job['id']}/skip", json={"reason": "manual"})

    assert response.status_code == 200
    data = response.json()
    assert data["job"]["status"] == "skipped"
    assert data["job"]["last_error"] == "manual"
    assert data["processed"] is None


def test_run_endpoint_queues_staged_jobs(tmp_path):
    """Verify manual run accepts one staged job for background work."""
    client = TestClient(create_app(config(tmp_path)))
    client.post(
        "/api/jobs",
        json={"url": "https://youtube.com/watch?v=abc", "queue_only": True},
    )

    response = client.post("/api/run", json={"max_jobs": 1})

    assert response.status_code == 200
    assert response.json() == {"queued": 1}
    assert client.get("/api/jobs").json()["jobs"][0]["status"] == "queued"


def test_tracking_submission_and_controls(tmp_path):
    """Verify tracked playlist submission, validation, listing, and controls."""
    client = TestClient(create_app(config(tmp_path)))

    rejected = client.post(
        "/api/jobs",
        json={
            "url": "https://youtube.com/watch?v=abc",
            "track_playlist": True,
        },
    )
    assert rejected.status_code == 400

    invalid_interval = client.post(
        "/api/jobs",
        json={
            "url": "https://youtube.com/playlist?list=PL123",
            "track_playlist": True,
            "interval_seconds": 5400,
        },
    )
    assert invalid_interval.status_code == 400

    response = client.post(
        "/api/jobs",
        json={
            "url": "https://youtube.com/playlist?list=PL123",
            "track_playlist": True,
            "interval_seconds": 86400,
        },
    )
    assert response.status_code == 200
    assert response.json()["accepted"] is True
    playlist_id = response.json()["job"]["playlist_id"]

    tracked = client.get("/api/playlists/tracked").json()["playlists"]
    assert tracked[0]["id"] == playlist_id
    assert tracked[0]["interval_seconds"] == 86400

    paused = client.patch(
        f"/api/playlists/{playlist_id}",
        json={"paused": True, "interval_seconds": 172800},
    )
    assert paused.status_code == 200
    assert paused.json()["paused"] is True
    assert paused.json()["interval_seconds"] == 172800

    run_now = client.post(f"/api/playlists/{playlist_id}/check")
    assert run_now.status_code == 200
    assert run_now.json()["accepted"] is False


def test_playlist_items_endpoint_returns_inventory(tmp_path):
    """Verify playlist item responses expose membership and download state."""
    cfg = config(tmp_path)
    db = Database(cfg.db_path)
    job, _ = db.add_job(
        Source.YOUTUBE,
        "https://youtube.com/playlist?list=PL123",
        "https://youtube.com/playlist?list=PL123",
    )
    playlist = db.ensure_playlist(job)
    from media_dl.playlist import PlaylistEntry

    db.reconcile_playlist_items(
        playlist.id,
        [
            PlaylistEntry(
                source=Source.YOUTUBE,
                provider_id="abc",
                url="https://www.youtube.com/watch?v=abc",
                title="Song",
                artist="Artist",
                position=1,
            )
        ],
    )
    db.close()
    client = TestClient(create_app(cfg))

    response = client.get(f"/api/playlists/{playlist.id}/items")

    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["title"] == "Song"
    assert response.json()["items"][0]["artist"] == "Artist"


def test_import_queue_endpoint_imports_queue_files(tmp_path):
    """Verify queue import endpoint summarizes imported rows."""
    cfg = config(tmp_path)
    cfg.ensure_dirs()
    (cfg.queue_dir / "inbox.txt").write_text(
        "https://youtube.com/watch?v=abc\n"
        "https://youtube.com/watch?v=abc&utm_source=test\n"
        "https://example.com/nope\n",
        encoding="utf-8",
    )
    client = TestClient(create_app(cfg))

    response = client.post("/api/import-queue")

    assert response.status_code == 200
    data = response.json()
    assert data["imported"] == 1
    assert data["duplicates"] == 1
    assert data["errors"] == 1
    assert data["rows"][0]["job"]["status"] == "queued"
    assert data["rows"][1]["created"] is False
    assert data["rows"][2]["error"] == "unsupported url: https://example.com/nope"


def test_clear_history_endpoint_deletes_jobs_and_archive(tmp_path):
    """Verify clear-history endpoint deletes jobs and the YouTube archive."""
    cfg = config(tmp_path)
    cfg.ensure_dirs()
    cfg.yt_archive_path.write_text("youtube abc\n", encoding="utf-8")
    client = TestClient(create_app(cfg))
    client.post(
        "/api/jobs",
        json={"url": "https://youtube.com/watch?v=abc", "queue_only": True},
    )

    response = client.post("/api/jobs/clear-history")

    assert response.status_code == 200
    assert response.json() == {"deleted_jobs": 1, "archive_deleted": True}
    assert client.get("/api/jobs").json()["total"] == 0
    assert not cfg.yt_archive_path.exists()


def test_clear_history_endpoint_rejects_running_job(tmp_path):
    """Verify clear-history endpoint rejects cleanup while a job is running."""
    cfg = config(tmp_path)
    cfg.ensure_dirs()
    db = Database(cfg.db_path)
    job, _ = db.add_job(Source.YOUTUBE, "raw", "normalized")
    db.mark_running(job.id)
    db.close()
    client = TestClient(create_app(cfg))

    response = client.post("/api/jobs/clear-history")

    assert response.status_code == 409
    assert response.json() == {"detail": "cannot clear history while a job is running"}
