# Requirements

## 1. Architecture Decision

- The long-running Docker service is API/dashboard-first.
  - It starts with `media-dl serve`.
  - It does not poll queue files automatically.
  - It does not start downloads unless a CLI command, API request, or dashboard action asks it to.
- Docker remains the runtime boundary.
  - Host users should not need local downloader dependencies.
  - The service owns `yt-dlp`, `spotDL`, `ffmpeg`, Deno, Python dependencies, and mounted storage paths.
- Localhost-only exposure is the default.
  - Compose binds `127.0.0.1:${SERVICE_PORT:-8765}:${SERVICE_PORT:-8765}`.
  - Multi-user auth and LAN exposure are out of scope for v1.

## 2. Completed Features

### 2.1 Docker Service Runtime

- Docker Compose builds and runs the `downloader` service as `navidrome-media-dl`.
- The service hosts FastAPI and the React dashboard on the same localhost port.
- Runtime endpoints exist:
  - `GET /api/health`
  - `GET /api/runtime`
- The mounted storage model is:
  - `/music`
  - `/state`
  - `/queue`
  - `/downloads`
  - `/config`
- Download concurrency protection uses `/state/media-dl.lock`.
  - This prevents overlapping downloads across the service container and one-off `docker compose run` containers sharing the same state mount.

### 2.2 Downloader Core

- URL classification supports:
  - YouTube
  - YouTube Music
  - Spotify
  - unsupported URL errors
- SQLite job state stores:
  - raw and normalized URLs
  - source
  - status
  - attempts
  - errors and warnings
  - output path
  - dedupe state
  - duplicate metadata
  - parent/child playlist summary fields
- YouTube downloads use `yt-dlp`.
  - Audio extraction, source metadata embedding, source thumbnails, default thumbnails, and no-thumbnail mode are supported.
  - Normal YouTube jobs use `/state/yt-dlp.archive`.
  - Forced duplicates bypass the archive.
- Spotify downloads use `spotDL`.
  - Spotify is used as catalog/metadata source.
  - Matched audio comes from YouTube/YouTube Music.
- Finished playlists with unavailable items can complete with a warning when unrelated downloaded items completed.

### 2.3 Runtime Settings And Planning

- Runtime settings persist to `/state/runtime-settings.json`.
- Dashboard/API settings include:
  - audio format
  - thumbnail mode
  - output layout
  - metadata mode
  - playlist handling
- Shared planning models exist:
  - `DownloadPlan`
  - `OutputLayout`
  - `MetadataPolicy`
  - `PlaylistPolicy`
- Default output layout is Navidrome-oriented:
  - `output_layout=artist_album_folders`
  - YouTube falls back to creator folders when album metadata is unavailable.
  - Source folders remain available as legacy/provenance mode.
- Metadata cleanup is complete for supported audio formats.
  - `metadata_mode=navidrome_clean` is the default.
  - Missing artist falls back to `Unknown Artist`.
  - Missing title falls back to the filename stem.
  - Missing album artist copies artist.
  - Missing album is left blank.
  - Cleanup warnings do not fail otherwise successful jobs.

### 2.4 Playlist Expansion

- `playlist_mode=single_job` preserves legacy single-job playlist downloads.
- `playlist_mode=expand_items` supports YouTube and YouTube Music playlists.
- Expansion behavior:
  - Parent playlist job runs flat `yt-dlp` extraction.
  - One queued child job is created per new extracted video URL.
  - Existing child URLs are counted as duplicates and are not attached.
  - Parent completes as a summary row with child counts.
  - Child jobs process later through manual run or normal job actions.
- Spotify playlists intentionally keep single-job behavior until Spotify-specific expansion is designed.

### 2.5 API, CLI, And Dashboard

- Shared job workflow functions back both API and container CLI:
  - add URL
  - retry
  - skip
  - list jobs
  - import queue files
  - run queued jobs
  - clear history
- Container-internal CLI commands exist:
  - `add`
  - `status`
  - `retry`
  - `skip`
  - `run-once`
  - `import-queue`
  - `clear-history --yes`
  - `watch`
  - `serve`
- Dashboard supports:
  - URL submission
  - queue-only toggle
  - forced duplicate toggle
  - runtime settings
  - paginated job history
  - parent/child playlist context
  - manual run
  - retry
  - skip
  - queue import
  - clear history
- Clear history deletes:
  - SQLite job history
  - URL dedupe history
  - `/state/yt-dlp.archive`
- Clear history preserves:
  - music files
  - runtime settings
  - queue files
  - config files

### 2.6 Tests And Verification

- Unit-style tests cover:
  - URL classification
  - SQLite schema migration and dedupe
  - clear-history behavior
  - downloader command construction
  - thumbnail modes
  - metadata cleanup
  - playlist extraction parsing
  - worker behavior
  - runtime settings
  - API endpoints
  - container-internal CLI safety checks
  - host CLI API commands and slash-command dispatch
- Regular verification path:
  - `python -m compileall src tests`
  - Docker pytest suite
  - `npm run build`
  - `docker compose build downloader`

## 3. Current CLI State

### 3.1 Host CLI Tool

- Repo-owned host wrapper executable exists at `bin/media-dl`.
- It can be run from the repo or symlinked into `~/.local/bin/media-dl`.
- It lets users run downloader operations without typing `docker compose run`.
- With no arguments it opens a simple slash-command shell.
- API-backed commands include:
  - `add`
  - `status`
  - `run`
  - `import-queue`
  - `retry`
  - `skip`
  - `settings`
  - `set`
  - `clear-history -f`
- Docker lifecycle commands include:
  - `up`
  - `down`
  - `rebuild`
  - `logs`
- Utilities include:
  - `open`
  - `doctor`
- `doctor` checks Docker, Compose, service health, runtime paths, and container tools.
- If an API-backed command runs while the service is down in an interactive terminal, the CLI offers to start Compose and retry.

### 3.2 Real-World Validation

- Validate `metadata_mode=navidrome_clean` with real YouTube, YouTube Music, and Spotify downloads.
- Validate output layout with real album/artist metadata gaps.
- Add source-specific metadata extraction improvements only after real edge cases appear.
- Keep metadata cleanup separate from command generation.

### 3.3 Documentation And Troubleshooting

- Keep README aligned with current UI/API/CLI behavior.
- Keep troubleshooting notes current for:
  - service not running
  - port already in use
  - stale browser bundle
  - bind-mounted state surviving `down -v`
  - YouTube unavailable videos
  - Spotify bad matches
  - Navidrome scan delay
  - missing default artwork

## 4. Dependency Graph

```text
Docker Compose service [complete]
+-- Existing Python downloader core [complete]
+-- Existing mounted storage model [complete]
+-- FastAPI API runtime [complete]
|   +-- Health/runtime endpoints [complete]
|   +-- Job API endpoints [complete]
|   +-- Settings endpoints [complete]
|   +-- Clear-history endpoint [complete]
+-- Shared service layer [complete]
|   +-- SQLite jobs [complete]
|   +-- URL classification [complete]
|   +-- yt-dlp runner [complete]
|   +-- spotDL runner [complete]
|   +-- Playlist expansion [complete]
|   +-- API/CLI orchestration functions [complete]
+-- React/Vite web UI [complete]
|   +-- Localhost job API [complete]
|   +-- Runtime settings UI [complete]
|   +-- Parent/child job display [complete]
|   +-- Clear-history action [complete]
+-- Download planning interface [complete]
|   +-- Navidrome metadata cleanup [complete]
|   +-- Artist/album output layout [complete]
|   +-- Creator-folder fallback [complete]
+-- Host CLI wrapper [complete]
    +-- Docker Compose lifecycle commands [complete]
    +-- Localhost API job commands [complete]
    +-- Slash-command shell [complete]
    +-- Doctor checks [complete]
```

## 5. Recommended Build Order

1. Documentation and code comment refresh.
2. Host CLI wrapper lifecycle commands.
3. Host CLI API-backed job commands.
4. Host CLI `doctor` and smoke checks.
5. Real-world metadata/output validation.

## 6. Out Of Scope For V1

- Background polling in the long-running service.
- Multi-user authentication.
- LAN or internet exposure.
- Multiple concurrent download workers.
- Direct Spotify audio extraction.
- Navidrome database writes.
- Automatic bad-match correction for Spotify.
- Public Python package release.
