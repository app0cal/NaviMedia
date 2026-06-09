# Requirements

## 1. Architecture Decision

- The long-running Docker service is API-only.
  - It does not poll queue files automatically.
  - It does not start downloads unless a command or future API request asks it to.
  - Queue files remain available for manual import through explicit commands.
- Downloads are user/action triggered.
  - Current trigger: container-internal CLI commands such as `add`, `retry`, and `run-once`.
  - Future trigger: localhost API endpoints called by the web UI or host CLI.
- Docker remains the runtime boundary.
  - Host users should not need to install `yt-dlp`, `spotDL`, `ffmpeg`, Deno, or Python dependencies directly.
  - The service owns downloader dependencies and mounted storage paths.

## 2. Completed Features

### 2.1 Docker Service Runtime

- Docker Compose service exists.
  - Builds from the project Dockerfile.
  - Runs as container `navidrome-media-dl`.
  - Restarts unless stopped.
- Service starts with `media-dl serve`.
  - Replaces the previous default `media-dl watch`.
  - Runs a FastAPI/Uvicorn process.
  - Does not start a background polling worker.
- Localhost-only API binding exists.
  - Host URL: `http://127.0.0.1:8765`.
  - Compose binds `127.0.0.1:${SERVICE_PORT:-8765}:${SERVICE_PORT:-8765}`.
  - The API is not exposed to the LAN by default.
- Runtime endpoints exist.
  - `GET /api/health`
  - `GET /api/runtime`
- Current volume model is preserved.
  - `/music`
  - `/state`
  - `/queue`
  - `/downloads`
- Current downloader dependency model is preserved.
  - `yt-dlp`
  - `spotDL`
  - `ffmpeg`
  - Deno JavaScript runtime
  - `yt-dlp-ejs` through `yt-dlp[default]`
- Download concurrency protection exists.
  - Uses a shared lock file at `/state/media-dl.lock`.
  - Prevents overlapping downloads across the service container and one-off `docker compose run` containers sharing the same state mount.

### 2.2 Existing Downloader Core

- URL classification exists.
  - YouTube URLs.
  - YouTube Music URLs.
  - Spotify URLs.
  - Unsupported URL handling.
- SQLite job state exists.
  - Stores raw URL, normalized URL, source, status, attempts, last error, and output path.
  - Deduplicates by normalized URL.
- YouTube download command generation exists.
  - Uses `yt-dlp`.
  - Extracts audio.
  - Embeds metadata and thumbnail.
  - Uses `yt-dlp` archive state.
- Spotify download command generation exists.
  - Uses `spotDL`.
  - Uses Spotify as catalog/metadata source.
  - Downloads matched audio from YouTube/YouTube Music.
- Container-internal CLI exists.
  - `add`
  - `status`
  - `retry`
  - `skip`
  - `run-once`
  - `import-queue`
  - `watch`
  - `serve`
- Immediate command execution exists.
  - `add` processes immediately by default.
  - `retry` processes immediately by default.
  - `--queue-only` can stage a job without processing.
- Optional duplicate override exists.
  - Normal `add` deduplicates by normalized URL.
  - `add --allow-duplicate` creates a separate job for the same normalized URL.
  - Forced duplicate output is stored under `/music/Duplicates/<job-id>/`.
  - Forced YouTube duplicates bypass the shared `yt-dlp` archive so the duplicate can actually download.

### 2.3 Current Test And Smoke Coverage

- Unit-style tests exist for:
  - URL classification.
  - SQLite dedupe.
  - Downloader command construction.
  - Worker behavior.
  - Runtime health/config endpoints.
- Verified Docker runtime behavior:
  - Image builds.
  - Service starts with `media-dl serve`.
  - `/api/health` responds.
  - `/api/runtime` responds.
  - Service logs show Uvicorn startup only, not repeated polling.
  - Existing `docker compose run --rm downloader status` still works.

### 2.4 Shared Service Layer

- Shared job workflow module exists.
  - `add_url`
  - `retry_job`
  - `skip_job`
  - `list_recent_jobs`
  - `import_queue_files`
  - `run_queued_jobs`
- Container-internal CLI uses the shared job workflow module.
  - CLI remains a parser/output layer.
  - Future API endpoints can call the same functions.
- Structured service results exist.
  - `AddJobResult`
  - `JobActionResult`
- Unsupported URL handling is centralized.
  - `UnsupportedUrlError`
- Existing behavior is preserved.
  - Immediate add/retry remains default.
  - `--queue-only` still stages work.
  - `--allow-duplicate` still creates explicit duplicate jobs.

## 3. Future Goals

### 3.1 Localhost Job API

- Add job list endpoint.
  - `GET /api/jobs?limit=50`
  - Returns job ID, source, URL, status, attempts, output path, and error.
- Add job creation endpoint.
  - `POST /api/jobs`
  - Body includes URL, `queue_only`, and `allow_duplicate`.
  - Processes immediately unless `queue_only` is true.
- Add retry endpoint.
  - `POST /api/jobs/{id}/retry`
  - Processes immediately unless `queue_only` is true.
- Add skip endpoint.
  - `POST /api/jobs/{id}/skip`
  - Accepts a reason.
- Add manual run/import endpoint.
  - `POST /api/run`
  - Explicitly imports and processes queued work.
- Add config endpoint or extend runtime endpoint.
  - Returns music, state, queue, download paths, audio format, and service port.
  - Must not imply polling is active.

### 3.2 Localhost Web UI

- Add React/Vite frontend.
  - Depends on the localhost job API.
  - Built into the Docker image.
  - Served by the FastAPI service.
- Add dashboard page.
  - URL input.
  - YouTube/Spotify/unsupported source detection.
  - Queue-only toggle.
  - Submit/download action.
- Add job table.
  - Shows ID, source, status, attempts, URL, output path, and last error.
  - Supports refresh.
  - Supports retry.
  - Supports skip.
- Add operational feedback.
  - Loading state.
  - Submitted state.
  - Running state.
  - Failed state with readable error.
  - Empty state.
- Add runtime/config panel.
  - Shows service URL, music path, state path, queue path, download path, and audio format.
- Keep UI localhost-only and operational.
  - It should be a tool dashboard, not a marketing page.
  - It should prioritize repeated use and clear job state.

### 3.3 Host CLI Tool

- Add repo-owned CLI wrapper executable.
  - Suggested path: `bin/media-dl`.
  - Can be symlinked into `~/.local/bin/media-dl`.
  - Allows `media-dl ...` from any terminal path, similar to the feel of `codex`.
- Add install command.
  - `./bin/media-dl install`
  - Creates or updates the symlink.
- Add Docker lifecycle commands.
  - `media-dl up`
  - `media-dl down`
  - `media-dl rebuild`
  - `media-dl logs`
- Add API-backed job commands.
  - `media-dl add <url>`
  - `media-dl add --queue-only <url>`
  - `media-dl status`
  - `media-dl retry <job-id>`
  - `media-dl skip <job-id> --reason "..."`
  - `media-dl run`
- Add browser helper.
  - `media-dl open`
  - Opens or prints `http://127.0.0.1:8765`.
- Add diagnostics.
  - `media-dl doctor`
  - Checks Docker.
  - Checks Docker Compose.
  - Checks service health.
  - Checks configured volume paths.
  - Checks container tools: `yt-dlp`, `spotDL`, `ffmpeg`, and Deno.

### 3.4 Documentation

- Update README for the no-polling architecture.
  - Explain service/API mode.
  - Explain that downloads are explicit actions.
  - Explain queue files as manual import only.
  - Explain the future web UI.
  - Explain future host CLI install.
- Add usage examples.
  - Current Docker commands.
  - Future host CLI commands.
  - Future web UI workflow.
- Add troubleshooting notes.
  - YouTube unavailable video.
  - Missing Docker.
  - Service not running.
  - Port already in use.
  - Bad Spotify match.
  - Navidrome scan delay.

### 3.5 Tests And Verification

- Add API tests for future job endpoints.
  - Job list.
  - Add URL.
  - Duplicate URL behavior.
  - Queue-only behavior.
  - Retry.
  - Skip.
  - Manual run/import.
- Add CLI wrapper tests where practical.
  - Repo root detection.
  - Docker Compose command construction.
  - API request construction.
  - Error messages when the service is down.
- Add web UI verification.
  - API integration.
  - Job submission.
  - Status refresh.
  - Retry/skip actions.
  - Responsive localhost dashboard layout.
- Keep Docker smoke test path.
  - Build service.
  - Start service.
  - Check `/api/health`.
  - Add a known public YouTube URL through current CLI or future API.
  - Confirm job completes.
  - Confirm file appears under `navidromeVolume/music`.

## 4. Dependency Graph

```text
Docker Compose service [complete]
+-- Existing Python downloader core [complete]
+-- Existing mounted storage model [complete]
+-- FastAPI API runtime [partial]
|   +-- Health/runtime endpoints [complete]
|   +-- Job API endpoints [future]
|   +-- Manual queue import endpoint [future]
+-- Shared service layer [complete]
|   +-- SQLite jobs [complete]
|   +-- URL classification [complete]
|   +-- yt-dlp runner [complete]
|   +-- spotDL runner [complete]
|   +-- API/CLI orchestration functions [complete]
+-- React/Vite web UI [future]
|   +-- Localhost job API [depends on future API endpoints]
+-- Host CLI wrapper [future]
    +-- Docker Compose lifecycle commands [can be built before job API]
    +-- Localhost API job commands [depends on future API endpoints]
```

## 5. Recommended Build Order

1. Add localhost job API endpoints.
2. Add explicit manual queue import endpoint.
3. Add host CLI wrapper lifecycle commands.
4. Add host CLI API-backed job commands.
5. Add React/Vite web UI.
6. Serve built web UI from FastAPI.
7. Update README and troubleshooting docs.
8. Add API, CLI, web UI, and smoke tests.

## 6. Out Of Scope For V1

- Background polling in the long-running service.
- Multi-user authentication.
- LAN or internet exposure.
- Multiple concurrent download workers.
- Direct Spotify audio extraction.
- Navidrome database writes.
- Automatic bad-match correction for Spotify.
- Public Python package release.
