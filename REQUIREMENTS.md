# Requirements

## 1. Current Baseline

- Dockerized downloader service exists.
  - Depends on Docker Compose.
  - Uses mounted folders for `/music`, `/state`, `/queue`, and `/downloads`.
  - Includes `yt-dlp`, `spotDL`, `ffmpeg`, Deno, and `yt-dlp[default]`.
- Python core exists.
  - Classifies YouTube and Spotify URLs.
  - Stores jobs in SQLite.
  - Deduplicates normalized URLs.
  - Processes YouTube links with `yt-dlp`.
  - Processes Spotify links with `spotDL`.
  - Supports `add`, `status`, `retry`, `skip`, `run-once`, and `watch`.
- Mock Navidrome storage exists under `navidromeVolume/`.
  - `navidromeVolume/music`
  - `navidromeVolume/state`
  - `navidromeVolume/queue`
  - `navidromeVolume/downloads`

## 2. Target Feature Tree

### 2.1 Docker Service Runtime

- Convert the long-running container into an API service.
  - Depends on the existing Python core.
  - Depends on FastAPI or equivalent Python web framework.
  - Replaces default container command from `media-dl watch` to `media-dl serve`.
  - Keeps queue-file polling as a background fallback.
- Bind the service to localhost.
  - Depends on Docker Compose port mapping.
  - Default host URL: `http://127.0.0.1:8765`.
  - Must not expose the UI/API to the LAN by default.
- Preserve current downloader dependencies.
  - `yt-dlp`
  - `spotDL`
  - `ffmpeg`
  - Deno JavaScript runtime
  - `yt-dlp-ejs` through `yt-dlp[default]`
- Preserve current volume model.
  - Music output remains mounted at `/music`.
  - SQLite state remains mounted at `/state`.
  - Queue files remain mounted at `/queue`.
  - Temporary downloads remain mounted at `/downloads`.
- Enforce one active download job at a time.
  - Depends on shared worker/service logic.
  - Prevents concurrent `yt-dlp` and `spotDL` jobs from competing for bandwidth, CPU, and output paths.

### 2.2 Shared Service Layer

- Extract CLI behavior into reusable service functions.
  - Depends on current CLI logic.
  - Needed by both API endpoints and container-internal CLI commands.
- Add job creation service.
  - Classifies URL.
  - Normalizes URL.
  - Inserts or deduplicates in SQLite.
  - Immediately processes by default unless `queue_only` is set.
- Add job processing service.
  - Processes a specific job by ID.
  - Maintains current statuses: `queued`, `retry`, `running`, `complete`, `failed`, `skipped`.
  - Records attempts, output path, and last error.
- Add queue import service.
  - Reads `inbox.txt`, `youtube.txt`, and `spotify.txt`.
  - Deduplicates imported URLs.
  - Processes or stages imported jobs according to worker settings.
- Add status/query service.
  - Lists recent jobs.
  - Supports status filtering later if needed.
  - Returns structured job data for API and web UI.

### 2.3 Localhost API

- Add health endpoint.
  - `GET /api/health`
  - Reports service availability.
  - Used by CLI `doctor`.
- Add job list endpoint.
  - `GET /api/jobs?limit=50`
  - Depends on status/query service.
  - Returns job ID, source, URL, status, attempts, output path, and error.
- Add job creation endpoint.
  - `POST /api/jobs`
  - Body: URL and `queue_only`.
  - Depends on job creation service.
  - Immediately starts processing unless `queue_only` is true.
- Add retry endpoint.
  - `POST /api/jobs/{id}/retry`
  - Depends on job processing service.
  - Immediately processes unless `queue_only` is true.
- Add skip endpoint.
  - `POST /api/jobs/{id}/skip`
  - Depends on SQLite job state.
  - Accepts a reason.
- Add run endpoint.
  - `POST /api/run`
  - Depends on queue import and worker logic.
  - Processes queued work manually.
- Add config endpoint.
  - `GET /api/config`
  - Returns configured music, state, queue, download paths, audio format, and poll interval.
  - Used by web UI and CLI diagnostics.

### 2.4 Localhost Web UI

- Add React/Vite frontend.
  - Depends on Docker service API.
  - Built into the Docker image.
  - Served by the FastAPI service.
- Add main dashboard page.
  - URL: `http://127.0.0.1:8765`.
  - Shows URL input.
  - Shows source detection for YouTube, Spotify, or unsupported URL.
  - Shows queue-only toggle.
  - Shows submit/download action.
- Add job table.
  - Depends on `GET /api/jobs`.
  - Displays ID, source, status, attempts, URL, output path, and last error.
  - Supports refresh.
  - Supports retry action.
  - Supports skip action.
- Add status and feedback states.
  - Loading state while jobs are fetched.
  - Submitted state after adding a URL.
  - Running state while a job is processing.
  - Failed state with readable error text.
  - Empty state when no jobs exist.
- Add service/config panel.
  - Depends on `GET /api/config`.
  - Shows music root, queue path, state path, downloads path, audio format, and poll interval.
- Add minimal logs or recent errors area.
  - Depends on job status data for v1.
  - Can become a dedicated log endpoint later.
- Keep UI localhost-only and operational.
  - The page is a tool dashboard, not a marketing page.
  - Must prioritize readable tables, clear controls, and fast repeated use.

### 2.5 Host CLI Tool

- Add repo-owned CLI wrapper executable.
  - Suggested path: `bin/media-dl`.
  - Depends on Docker Compose.
  - Depends on the repo root containing `docker-compose.yml`.
  - Can be symlinked into `~/.local/bin/media-dl`.
- Add install command.
  - `./bin/media-dl install`
  - Creates or updates symlink on the host.
  - After install, user can run `media-dl` from any terminal path.
- Add lifecycle commands.
  - `media-dl up`
    - Runs `docker compose up -d --build`.
  - `media-dl down`
    - Runs `docker compose down`.
  - `media-dl rebuild`
    - Runs `docker compose build downloader`.
  - `media-dl logs`
    - Runs `docker compose logs -f downloader`.
- Add API-backed job commands.
  - `media-dl add <url>`
    - Calls `POST /api/jobs`.
  - `media-dl add --queue-only <url>`
    - Calls `POST /api/jobs` with `queue_only`.
  - `media-dl status`
    - Calls `GET /api/jobs`.
  - `media-dl retry <job-id>`
    - Calls `POST /api/jobs/{id}/retry`.
  - `media-dl skip <job-id> --reason "..."`
    - Calls `POST /api/jobs/{id}/skip`.
  - `media-dl run`
    - Calls `POST /api/run`.
- Add browser helper.
  - `media-dl open`
  - Opens `http://127.0.0.1:8765` where supported.
  - Prints the URL when automatic opening is unavailable.
- Add diagnostics command.
  - `media-dl doctor`
  - Checks Docker availability.
  - Checks Docker Compose availability.
  - Checks whether the service is running.
  - Checks API health.
  - Checks volume path existence.
  - Checks container tools: `yt-dlp`, `spotDL`, `ffmpeg`, and `deno`.
- Keep CLI behavior similar to `codex`.
  - User types `media-dl ...` directly after install.
  - The implementation can still be a repo script.
  - A Python package is optional later, not required for v1.

### 2.6 Documentation

- Update `README.md`.
  - Explain Docker service mode.
  - Explain localhost web UI.
  - Explain host CLI install.
  - Explain immediate add/download behavior.
  - Explain queue-only behavior.
- Add CLI examples.
  - `media-dl install`
  - `media-dl up`
  - `media-dl add <url>`
  - `media-dl status`
  - `media-dl open`
  - `media-dl doctor`
- Add troubleshooting notes.
  - YouTube unavailable video.
  - Missing Docker.
  - Service not running.
  - Port already in use.
  - Bad Spotify match.
  - Navidrome scan delay.

### 2.7 Tests And Verification

- Add API tests.
  - Health endpoint.
  - Job list endpoint.
  - Add URL endpoint.
  - Duplicate URL behavior.
  - Queue-only behavior.
  - Retry behavior.
  - Skip behavior.
  - Run endpoint.
- Add CLI wrapper tests where practical.
  - Command parsing.
  - Repo root detection.
  - API request construction.
  - Docker Compose command construction.
- Keep existing tests.
  - URL classification.
  - SQLite dedupe.
  - Downloader command generation.
  - Worker behavior.
- Add manual smoke test path.
  - Build service.
  - Start service.
  - Run `media-dl doctor`.
  - Add a known public YouTube URL.
  - Confirm job completes.
  - Confirm file appears under `navidromeVolume/music`.
  - Open web UI and confirm job appears in table.

## 3. Dependency Graph

```text
Docker Compose service
+-- Existing Python downloader core
+-- Existing mounted storage model
+-- FastAPI API service
|   +-- Shared service layer
|   |   +-- SQLite jobs
|   |   +-- URL classification
|   |   +-- yt-dlp runner
|   |   +-- spotDL runner
|   +-- Background queue worker
|   +-- Static React UI serving
+-- React/Vite web UI
|   +-- Localhost API endpoints
+-- Host CLI wrapper
    +-- Docker Compose lifecycle commands
    +-- Localhost API job commands
```

## 4. Recommended Build Order

1. Refactor Python CLI behavior into shared service functions.
2. Add FastAPI service and API endpoints.
3. Update Docker Compose to expose `127.0.0.1:8765`.
4. Add host CLI wrapper and `doctor`.
5. Add React/Vite web UI.
6. Serve built web UI from FastAPI.
7. Update documentation.
8. Add API, CLI, and smoke tests.

## 5. Out Of Scope For V1

- Multi-user authentication.
- LAN or internet exposure.
- Multiple concurrent download workers.
- Direct Spotify audio extraction.
- Navidrome database writes.
- Automatic bad-match correction for Spotify.
- Public Python package release.
