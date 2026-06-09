# Navidrome Media Downloader

Queue-based downloader for feeding a Navidrome music folder from YouTube and Spotify links.

This project stores audio only in the current implementation. YouTube links are downloaded with `yt-dlp`. Spotify links are handled by `spotDL`, which uses Spotify as the metadata/catalog source and downloads matched audio from YouTube Music/YouTube.

## Layout

```text
/music       Navidrome music library mount
/queue       watched input files
/state       SQLite database and yt-dlp archive
/downloads   temporary files and logs
```

The long-running Docker service exposes a localhost-only runtime API at:

```text
http://127.0.0.1:8765
```

The same address serves the web dashboard. If you set `SERVICE_PORT` in `.env`, open that port instead:

```text
http://127.0.0.1:${SERVICE_PORT}
```

Default output:

```text
/music/YouTube/<playlist-or-uploader>/<index> - <title>.m4a
/music/Spotify/<album-artist>/<album>/<track-number> - <title>.m4a
```

## Start

Create host folders:

```bash
mkdir -p music queue state downloads
```

Optionally point Compose at your existing Navidrome music folder:

```bash
cp .env.example .env
# edit .env so NAVIDROME_MUSIC_ROOT points at your real Navidrome music folder
docker compose up -d --build
```

Navidrome should mount the same host folder read-only or read-write as its music library.
The downloader also mounts `${NAVIMEDIA_CONFIG:-./config}` at `/config`; place an editable `default.jpg` there for the configured default thumbnail path.

## Queue Files

Queue files are available for manual import, but the long-running service does not poll them automatically. Paste links into any of these files:

```text
queue/inbox.txt
queue/youtube.txt
queue/spotify.txt
```

Each non-empty, non-comment line is imported. Duplicate normalized URLs are skipped.

## CLI

```bash
docker compose run --rm downloader add "https://youtube.com/watch?v=..."
docker compose run --rm downloader add "https://youtube.com/playlist?list=..."
docker compose run --rm downloader add "https://open.spotify.com/playlist/..."
docker compose run --rm downloader add --allow-duplicate "https://youtube.com/watch?v=..."
docker compose run --rm downloader status
docker compose run --rm downloader retry 12
docker compose run --rm downloader skip 12 --reason "video unavailable"
```

`add` and `retry` process their target job immediately by default. Use `--queue-only` if you want to stage work for a later manual run:

```bash
docker compose run --rm downloader add --queue-only "https://youtube.com/watch?v=..."
docker compose run --rm downloader run-once
```

Normal `add` deduplicates by normalized URL. `add --allow-duplicate` creates a separate job for the same URL and stores its output under `/music/Duplicates/<job-id>/`.

## Runtime API

The service starts with `media-dl serve`, which runs the localhost web dashboard and API on the same Compose port. Downloads happen only when a CLI command, web action, or API request asks for work.

Open the dashboard:

```bash
xdg-open http://127.0.0.1:8765
```

The dashboard can submit URLs, import queue files, manually run one queued job, retry, skip, and refresh recent job state. The left-side audio format control persists to `/state/runtime-settings.json` and supports `m4a`, `mp3`, `flac`, `opus`, and `wav`. The "Allow duplicate redownload" toggle maps to `allow_duplicate`; leave it off to dedupe by normalized URL, or turn it on to create a separate tracked duplicate job.

```bash
curl http://127.0.0.1:8765/api/health
curl http://127.0.0.1:8765/api/runtime
curl http://127.0.0.1:8765/api/jobs
curl http://127.0.0.1:8765/api/settings
```

## Notes

- Use this only for media you are allowed to download and store.
- Spotify is not downloaded directly from Spotify.
- The image includes Deno plus `yt-dlp[default]` because current YouTube extraction needs an external JavaScript runtime and EJS challenge solver support.
- `yt-dlp` also maintains `/state/yt-dlp.archive` so rerunning changed playlists skips items already downloaded.
- Navidrome does not write metadata to music files. This downloader writes tagged files before Navidrome scans them.
