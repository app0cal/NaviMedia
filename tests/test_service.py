from fastapi.testclient import TestClient

from media_dl.config import Config
import media_dl.service as service
from media_dl.service import create_app


def config(tmp_path):
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
    client = TestClient(create_app(config(tmp_path)))

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_runtime_endpoint(tmp_path):
    cfg = config(tmp_path)
    client = TestClient(create_app(cfg))

    response = client.get("/api/runtime")

    assert response.status_code == 200
    data = response.json()
    assert data["music_root"] == str(cfg.music_root)
    assert data["state_dir"] == str(cfg.state_dir)
    assert data["queue_dir"] == str(cfg.queue_dir)
    assert data["download_dir"] == str(cfg.download_dir)
    assert "poll_seconds" not in data
    assert data["service_port"] == 8765
    assert data["audio_format"] == "m4a"


def test_settings_endpoint_defaults_and_lists_allowed_formats(tmp_path):
    client = TestClient(create_app(config(tmp_path)))

    response = client.get("/api/settings")

    assert response.status_code == 200
    assert response.json() == {
        "audio_format": "m4a",
        "audio_formats": ["m4a", "mp3", "flac", "opus", "wav"],
    }


def test_settings_endpoint_persists_audio_format(tmp_path):
    cfg = config(tmp_path)
    client = TestClient(create_app(cfg))

    response = client.put("/api/settings", json={"audio_format": "mp3"})

    assert response.status_code == 200
    assert response.json() == {
        "audio_format": "mp3",
        "audio_formats": ["m4a", "mp3", "flac", "opus", "wav"],
    }
    assert client.get("/api/runtime").json()["audio_format"] == "mp3"
    assert TestClient(create_app(cfg)).get("/api/settings").json()["audio_format"] == "mp3"


def test_settings_endpoint_rejects_unsupported_audio_format(tmp_path):
    client = TestClient(create_app(config(tmp_path)))

    response = client.put("/api/settings", json={"audio_format": "aac"})

    assert response.status_code == 400
    assert response.json() == {"detail": "unsupported audio format: aac"}


def test_web_index_served_when_built(tmp_path, monkeypatch):
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
    web_dir = tmp_path / "web-dist"
    web_dir.mkdir()
    (web_dir / "index.html").write_text("<html><body>dashboard</body></html>", encoding="utf-8")
    monkeypatch.setattr(service, "WEB_DIST_DIR", web_dir)
    client = TestClient(create_app(config(tmp_path)))

    response = client.get("/jobs/123")

    assert response.status_code == 200
    assert "dashboard" in response.text


def test_unknown_api_path_stays_api_404(tmp_path, monkeypatch):
    web_dir = tmp_path / "web-dist"
    web_dir.mkdir()
    (web_dir / "index.html").write_text("<html><body>dashboard</body></html>", encoding="utf-8")
    monkeypatch.setattr(service, "WEB_DIST_DIR", web_dir)
    client = TestClient(create_app(config(tmp_path)))

    response = client.get("/api/not-found")

    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}


def test_jobs_endpoint_returns_existing_jobs(tmp_path):
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
    jobs = response.json()["jobs"]
    assert [job["id"] for job in jobs] == [second["id"], first["id"]]
    assert jobs[0]["source"] == "youtube"


def test_create_job_endpoint_creates_and_processes_job(tmp_path):
    client = TestClient(create_app(config(tmp_path)))

    response = client.post("/api/jobs", json={"url": "https://youtube.com/watch?v=abc"})

    assert response.status_code == 200
    data = response.json()
    assert data["created"] is True
    assert data["job"]["status"] == "queued"
    assert data["processed"]["status"] == "failed"
    assert data["processed"]["last_error"]


def test_create_job_endpoint_dedupes_by_default(tmp_path):
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
    client = TestClient(create_app(config(tmp_path)))

    response = client.post("/api/jobs", json={"url": "https://example.com/item"})

    assert response.status_code == 400
    assert response.json() == {"detail": "unsupported url: https://example.com/item"}


def test_retry_endpoint_marks_retry_and_respects_queue_only(tmp_path):
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
    client = TestClient(create_app(config(tmp_path)))

    response = client.post("/api/jobs/404/retry", json={"queue_only": True})

    assert response.status_code == 404


def test_skip_endpoint_marks_skipped_with_reason(tmp_path):
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


def test_run_endpoint_processes_queued_jobs(tmp_path):
    client = TestClient(create_app(config(tmp_path)))
    client.post(
        "/api/jobs",
        json={"url": "https://youtube.com/watch?v=abc", "queue_only": True},
    )

    response = client.post("/api/run", json={"max_jobs": 1})

    assert response.status_code == 200
    assert response.json() == {"processed": 1}
    assert client.get("/api/jobs").json()["jobs"][0]["status"] == "failed"


def test_import_queue_endpoint_imports_queue_files(tmp_path):
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
