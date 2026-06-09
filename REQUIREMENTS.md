# Requirements

## 1. Architecture Decision

- The long-running Docker service is API-only.
  - It does not poll queue files automatically.
  - It does not start downloads unless a CLI command, API request, or web UI action asks it to.
  - Queue files remain available for manual import through explicit commands.
- Downloads are user/action triggered.
  - Current triggers: container-internal CLI commands, localhost API endpoints, and the bundled web UI.
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
- Localhost job API exists.
  - Add, list, retry, skip, manual run, and explicit queue import are available under `/api`.
  - Job listing is paginated and bounded.
  - Immediate processing remains synchronous unless `queue_only` is requested.
- Current volume model is preserved.
  - `/music`
  - `/state`
  - `/queue`
  - `/downloads`
  - `/config`
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
  - Stores URL, source, status, attempts, errors, warnings, output path, dedupe state, and duplicate metadata.
  - Deduplicates by normalized URL.
- YouTube download command generation exists.
  - Uses `yt-dlp`.
  - Extracts audio.
  - Embeds source metadata.
  - Supports source thumbnails, default thumbnail, or no thumbnails.
  - Uses `yt-dlp` archive state.
- Spotify download command generation exists.
  - Uses `spotDL`.
  - Uses Spotify as catalog/metadata source.
  - Downloads matched audio from YouTube/YouTube Music.
  - Supports the same thumbnail mode setting where supported by the current post-processing path.
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
- Runtime settings exist.
  - Audio format can be changed from the API/web UI.
  - Thumbnail mode can be changed from the API/web UI.
  - Metadata cleanup mode can be changed from the API/web UI.
  - Default thumbnail path is `/config/default.jpg`.
- Partial playlist warnings exist.
  - Finished playlists with unavailable items can complete with a warning instead of failing unrelated downloaded items.
- Navidrome-friendly metadata cleanup exists as an opt-in mode.
  - `metadata_mode=source` remains the default.
  - `metadata_mode=navidrome_clean` runs after successful downloads.
  - Missing artist falls back to `Unknown Artist`.
  - Missing title falls back to the filename stem.
  - Missing album artist copies artist.
  - Missing album is left blank.
  - Metadata cleanup warnings do not fail otherwise successful jobs.

### 2.3 Current Test And Smoke Coverage

- Unit-style tests exist for:
  - URL classification.
  - SQLite dedupe.
  - Downloader command construction.
  - Worker behavior.
  - Runtime health/config endpoints.
  - Job API endpoints.
  - Runtime settings.
  - Thumbnail modes.
  - Paginated job listing.
- Verified Docker runtime behavior:
  - Image builds.
  - Service starts with `media-dl serve`.
  - `/api/health` responds.
  - `/api/runtime` responds.
  - `/api/settings` responds.
  - `/api/jobs` responds.
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
  - API endpoints call the same functions.
- Structured service results exist.
  - `AddJobResult`
  - `JobActionResult`
- Unsupported URL handling is centralized.
  - `UnsupportedUrlError`
- Existing behavior is preserved.
  - Immediate add/retry remains default.
  - `--queue-only` still stages work.
  - `--allow-duplicate` still creates explicit duplicate jobs.

### 2.5 Bundled Localhost Dashboard

- React/Vite dashboard exists.
  - Built into the Docker image and served by FastAPI.
  - Shares the same localhost Compose port as the API.
- Dashboard supports:
  - URL submission.
  - Queue-only toggle.
  - Forced duplicate toggle.
  - Paginated job history.
  - Retry, skip, manual run, and explicit queue import.
  - Audio format, thumbnail mode, metadata mode, and advanced library layout settings.
- Dashboard is operational UI, not a marketing page.
  - Uses a darker-light theme.
  - Keeps localhost-only service exposure.

### 2.6 Shared Download Planning Interface

- Shared planning models exist.
  - `DownloadPlan`
  - `OutputLayout`
  - `MetadataPolicy`
  - `PlaylistPolicy`
- Downloader command generation uses the shared plan.
  - YouTube and Spotify commands derive output templates from the same interface.
  - Job completion output paths come from the same plan.
- Future policy settings exist.
  - `output_layout`
  - `metadata_mode`
  - `playlist_mode`
- Current default layout remains source folders until metadata cleanup is reliable.
  - Source folders are treated as legacy/provenance mode.
  - Artist/album folders are the Navidrome target.
  - Creator folders are the fallback where album metadata is unavailable.

## 3. Future Goals

### 3.1 Host CLI Tool

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

### 3.2 Navidrome-Friendly Metadata Follow-Ups

- Validate `metadata_mode=navidrome_clean` with real YouTube, YouTube Music, and Spotify downloads.
- Add any source-specific metadata extraction needed before changing defaults.
- Keep metadata cleanup separate from command generation.
  - Metadata cleanup runs after yt-dlp or spotDL finishes.
  - It reuses the mutagen-based post-processing path used for default thumbnails.
- Do not make `navidrome_clean` the default until artist/album behavior is reliable.

### 3.3 Root Music Output Layout

- Promote Navidrome-friendly output layout.
  - Preferred target: `/music/<artist>/<album>/<track> - <title>.<ext>`.
  - If album is missing, use `/music/<artist>/<title>.<ext>`.
  - If artist/album metadata is unreliable, fall back to `/music/<creator>/<title>.<ext>`.
  - Do not use `/music/YouTube/...` or `/music/Spotify/...` as the primary layout once metadata cleanup is reliable.
- Preserve duplicates.
  - Forced duplicates should still go under `/music/Duplicates/<job-id>/...`.
- Runtime setting exists and needs full behavior support.
  - `output_layout=source_folders`
  - `output_layout=creator_folders`
  - `output_layout=artist_album_folders`

### 3.4 Playlist Item Expansion

- Add optional playlist expansion.
  - Extract playlist items first.
  - Create one queued job per item.
  - Let each item use its own creator path and metadata cleanup.
- Track parent/child jobs.
  - The original playlist job should be visible as the parent.
  - Child jobs should show individual success/failure state.
- Add a future runtime/request setting.
  - `playlist_mode=single_job`
  - `playlist_mode=expand_items`

### 3.5 Planning Interface Follow-Ups

- Continue using the completed shared planning interface as the boundary for future work.
  - Metadata cleanup should consume `MetadataPolicy`.
  - Output layout promotion should consume `OutputLayout`.
  - Playlist expansion should consume `PlaylistPolicy`.
- Keep new CLI, API, and web UI behavior routed through the same runtime settings.
  - Avoid adding separate one-off layout or metadata decisions inside command generation.

### 3.6 Documentation

- Update README for:
  - Current API/web dashboard behavior.
  - Thumbnail modes and `/config/default.jpg`.
  - Paginated job history.
  - Future host CLI install.
  - Future Navidrome metadata/output layout options.
- Add troubleshooting notes.
  - YouTube unavailable video.
  - Missing Docker.
  - Service not running.
  - Port already in use.
  - Bad Spotify match.
  - Navidrome scan delay.
  - Missing or invalid default thumbnail.

### 3.7 Tests And Verification

- Add CLI wrapper tests where practical.
  - Repo root detection.
  - Docker Compose command construction.
  - API request construction.
  - Error messages when the service is down.
- Add future metadata/output/playlist tests.
  - Navidrome tag normalization.
  - Playlist parent/child job creation.
  - Full artist/album output behavior.
  - Creator-folder fallback behavior with real metadata gaps.
- Keep Docker smoke test path.
  - Build service.
  - Start service.
  - Check `/api/health`.
  - Add a known public YouTube URL through the CLI or API.
  - Confirm job completes.
  - Confirm file appears under `navidromeVolume/music`.

## 4. Dependency Graph

```text
Docker Compose service [complete]
+-- Existing Python downloader core [complete]
+-- Existing mounted storage model [complete]
+-- FastAPI API runtime [complete]
|   +-- Health/runtime endpoints [complete]
|   +-- Job API endpoints [complete]
|   +-- Manual queue import endpoint [complete]
+-- Shared service layer [complete]
|   +-- SQLite jobs [complete]
|   +-- URL classification [complete]
|   +-- yt-dlp runner [complete]
|   +-- spotDL runner [complete]
|   +-- API/CLI orchestration functions [complete]
+-- React/Vite web UI [complete]
|   +-- Localhost job API [complete]
+-- Download planning interface [complete]
|   +-- Navidrome metadata cleanup [complete, opt-in]
|   +-- Artist/album output layout [partial, depends on metadata validation]
|   +-- Creator-folder output layout [partial]
|   +-- Playlist item expansion [future]
+-- Host CLI wrapper [future]
    +-- Docker Compose lifecycle commands [can be built before job API]
    +-- Localhost API job commands [can use completed API endpoints]
```

## 5. Recommended Build Order

1. Validate Navidrome metadata cleanup with real downloads.
2. Promote artist/album output layout as the recommended default.
3. Complete creator-folder fallback behavior for metadata gaps.
4. Add playlist item expansion mode.
5. Add host CLI wrapper lifecycle commands.
6. Add host CLI API-backed job commands.
7. Update README and troubleshooting docs.
8. Add metadata/output/playlist, CLI, and smoke tests.

## 6. Out Of Scope For V1

- Background polling in the long-running service.
- Multi-user authentication.
- LAN or internet exposure.
- Multiple concurrent download workers.
- Direct Spotify audio extraction.
- Navidrome database writes.
- Automatic bad-match correction for Spotify.
- Public Python package release.
