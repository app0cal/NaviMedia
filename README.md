# Navidrome Media Downloader
Media downloader for installing audio from YouTube, YouTube Music, and Spotify links with the primary design for writing metadata for Navidrome.

The project was created to supplement the issue of getting all the main audio files for Navidrome for homelabs. Host users should not need to install `yt-dlp`, `spotDL`, `ffmpeg`, Deno, or Python dependencies directly. The container serves a localhost-only API and the bundled web dashboard. Downloads are handled sequentially by one background worker after a CLI/API/dashboard request or a due tracked-playlist check.

WARNING: for those who are hosting this on a homelab do NOT expose this service. Simple security logins will be added for the next update, but this is intended to be on a local computer only (ngl i mostly made this shit for myself). 

## Layout

```text
/music       Navidrome music library mount
/queue       optional text queue files
/state       SQLite database, runtime settings, lock file, and yt-dlp archive
/downloads   temporary download files
/config      default artwork and app config files
```

The service and dashboard are available at:

```text
http://localhost:SERVICE_PORT
```

Refer to .env.example to see the ideal .env or docker env setup.

Default output is organized for Navidrome indexing:

```text
/music/<artist-or-creator>/<title>.formatType
/music/<artist>/<album>/<track-number> - <title>.formatType
```

The legacy source-folder layout is still available from settings:

```text
/music/YouTube/<playlist-or-uploader>/<index> - <title>.formatType
/music/Spotify/<album-artist>/<album>/<track-number> - <title>.formatType
```

## Start

Create host folders:

```bash
mkdir -p music queue state downloads config
```

Optionally point Compose at your existing Navidrome music folder:

```bash
cp .env.example .env
# edit .env so NAVIDROME_MUSIC_ROOT points at your real Navidrome music folder
# set PUID and PGID to the output of `id -u` and `id -g`
./bin/media-dl up
```

Navidrome should mount the same host folder as its music library. The downloader also mounts `${NAVIMEDIA_CONFIG:-./config}` at `/config`; place `default.jpg` there if you use default artwork mode.
Compose runs the downloader as `${PUID:-1000}:${PGID:-1000}` so files created
through bind mounts remain manageable by the host account.

Use `./bin/media-dl rebuild` when you want the same behavior as `docker compose up -d --build`.

## Host CLI

The host CLI is the preferred way to operate the downloader from this repo:

```bash
./bin/media-dl up
./bin/media-dl open
./bin/media-dl add "https://youtube.com/watch?v=..."
./bin/media-dl add --queue-only "https://youtube.com/playlist?list=..."
./bin/media-dl status
./bin/media-dl run
./bin/media-dl settings
./bin/media-dl set format opus
./bin/media-dl clear-history -f
```

With no arguments, it opens a simple slash-command shell:

```text
./bin/media-dl
media-dl> /settings
media-dl> /set format opus
media-dl> /add https://youtube.com/watch?v=...
media-dl> /status
media-dl> /exit
```

`settings` and `/settings` show current values and allowed options. Friendly setting names are available for `set` and `/set`:

```text
format     audio_format
thumbnail  thumbnail_mode
layout     output_layout
metadata   metadata_mode
playlist   playlist_mode
```

The CLI talks to the localhost API by default. If the service is down and the terminal is interactive, it offers to start Docker Compose and retry the command. Set `MEDIA_DL_URL` or pass `--url` to target another service URL.

## Dashboard

Open:

```bash
./bin/media-dl open
```

The dashboard supports:

- Submit YouTube, YouTube Music, and Spotify URLs.
- Track public or unlisted playlists on daily, weekly, 30-day, or custom schedules.
- Run, pause, resume, reschedule, or stop tracked playlists.
- Expand playlist rows to inspect the latest title, artist, membership, and download state.
- Receive live job and playlist updates through server-sent events without page reloads.
- Queue-only submission for later manual runs.
- Forced duplicate redownloads under `/music/Duplicates/<job-id>/`.
- Paginated job history with parent/child playlist context.
- Retry, skip, manual run, explicit queue import, and clear history.
- Runtime settings for audio format, thumbnails, library layout, metadata cleanup, and playlist handling.

Runtime settings persist to:

```text
/state/runtime-settings.json
```

## Playlist Handling

Playlist handling is controlled by the dashboard setting `Playlist handling`.

- `Download playlist as one job` keeps current single-job behavior.
- `Expand playlist into queued items` currently applies to YouTube and YouTube Music playlists.

Every playlist submission records the latest source membership for the expandable dashboard view. YouTube inventory uses flat `yt-dlp` metadata and Spotify inventory uses spotDL metadata-only output.

When expansion is enabled, a one-time YouTube playlist creates one queued child job per new item and completes as a summary row. Child jobs are processed later by manual runs or normal job actions. Untracked Spotify playlists retain single-job download behavior.

Tracked YouTube and Spotify playlists always use item-level jobs, regardless of this setting. A tracked playlist downloads its current items on the initial check and later queues only new or previously failed items. Removed source items are marked as removed in the dashboard; local music is never deleted.

Tracking defaults to once a week. Schedules are stored in UTC and calculated from the completion of the last check. `Run now` resets that interval. After downtime, the service queues one catch-up check for each overdue playlist rather than replaying every missed interval.

Only public and unlisted playlists accessible without new provider credentials are supported.

## Clear History

The dashboard `Clear history` action clears:

- SQLite job history.
- URL dedupe history.
- `/state/yt-dlp.archive`.

It keeps:

- Downloaded music under `/music`.
- Runtime settings under `/state/runtime-settings.json`.
- Queue files under `/queue`.
- Config files under `/config`.
- Tracked playlist definitions, schedules, current membership, and known media state.

This is the recommended way to get a fresh job history without deleting music.

## Queue Files

Queue files are available for manual import, but the service does not poll them automatically. Paste links into any of these files:

```text
queue/inbox.txt
queue/youtube.txt
queue/spotify.txt
```

Each non-empty, non-comment line is imported. Duplicate normalized URLs are skipped unless the job was intentionally submitted as a duplicate through another path.

## Container CLI

The lower-level package CLI still runs inside the Docker container:

```bash
docker compose run --rm downloader add "https://youtube.com/watch?v=..."
docker compose run --rm downloader add "https://youtube.com/playlist?list=..."
docker compose run --rm downloader add "https://open.spotify.com/playlist/..."
docker compose run --rm downloader add --allow-duplicate "https://youtube.com/watch?v=..."
docker compose run --rm downloader status
docker compose run --rm downloader retry 12
docker compose run --rm downloader skip 12 --reason "video unavailable"
docker compose run --rm downloader clear-history --yes
```

`add` and `retry` process their target job immediately by default. Use `--queue-only` to stage work for a later manual run:

```bash
docker compose run --rm downloader add --queue-only "https://youtube.com/watch?v=..."
docker compose run --rm downloader run-once
```

Most users should prefer `./bin/media-dl ...` from the host. Use the container CLI only when debugging the image entrypoint or bypassing the service API.

## Runtime API

Useful endpoints:

```bash
curl http://127.0.0.1:8765/api/health
curl http://127.0.0.1:8765/api/runtime
curl http://127.0.0.1:8765/api/jobs
curl http://127.0.0.1:8765/api/settings
```

Job and operation endpoints include:

```text
POST /api/jobs
POST /api/jobs/{job_id}/retry
POST /api/jobs/{job_id}/skip
POST /api/run
POST /api/import-queue
POST /api/jobs/clear-history
GET  /api/playlists/tracked
GET  /api/playlists/{playlist_id}/items
PATCH /api/playlists/{playlist_id}
POST /api/playlists/{playlist_id}/check
GET  /api/events
```

Service job actions are asynchronous: successful responses report accepted/queued work, and the background worker updates job state afterward. `/api/events` is an SSE notification stream; REST responses remain the authoritative state and clients should refresh them after reconnecting.

## Troubleshooting

- **State survived `docker compose down -v`:** this project uses bind-mounted host folders, so Docker does not delete `./state`, `./downloads`, or `./music`.
- **Service unavailable:** check `docker compose ps` and `curl http://localhost:SERVICE_PORT/api/health`.
- **YouTube URL is skipped after clearing jobs manually:** use dashboard `Clear history` or remove `/state/yt-dlp.archive`.
- **Files downloaded but Navidrome does not show them yet:** wait for or trigger a Navidrome library scan.
- **Default artwork warning:** ensure `/config/default.jpg` exists and is a JPG or PNG when thumbnail mode is set to default artwork.
- **Tracked playlist stopped updating:** confirm it is not paused, inspect its last inventory error, and use `Run now` after provider connectivity returns.
- **Live updates disconnected:** the dashboard reconnects automatically and reloads REST state; the explicit Refresh action remains available.
- **Downloaded files cannot be deleted on the host:** set `PUID` and `PGID` in
  `.env` to `id -u` and `id -g`, then repair older files with
  `docker exec -u 0 navidrome-media-dl chown -R "$(id -u):$(id -g)" /music /state /queue /downloads /config`.

## Notes

- Use this only for media you are allowed to download and store.
- Spotify is not downloaded directly from Spotify; `spotDL` uses Spotify metadata and downloads matched audio from YouTube/YouTube Music.
- The image includes Deno plus `yt-dlp[default]` because current YouTube extraction needs an external JavaScript runtime and EJS challenge solver support.
- Navidrome does not write metadata to music files. This downloader writes tagged files before Navidrome scans them.
