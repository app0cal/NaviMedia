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

## Queue Links

Paste links into any of these files:

```text
queue/inbox.txt
queue/youtube.txt
queue/spotify.txt
```

Each non-empty, non-comment line is imported. Duplicate normalized URLs are skipped.
The daemon checks queue files every 5 seconds by default.

## CLI

```bash
docker compose run --rm downloader add "https://youtube.com/watch?v=..."
docker compose run --rm downloader add "https://youtube.com/playlist?list=..."
docker compose run --rm downloader add "https://open.spotify.com/playlist/..."
docker compose run --rm downloader status
docker compose run --rm downloader retry 12
docker compose run --rm downloader skip 12 --reason "video unavailable"
```

`add` and `retry` process their target job immediately by default. Use `--queue-only` if you want to stage work for the daemon instead:

```bash
docker compose run --rm downloader add --queue-only "https://youtube.com/watch?v=..."
docker compose run --rm downloader run-once
```

## Notes

- Use this only for media you are allowed to download and store.
- Spotify is not downloaded directly from Spotify.
- The image includes Deno plus `yt-dlp[default]` because current YouTube extraction needs an external JavaScript runtime and EJS challenge solver support.
- `yt-dlp` also maintains `/state/yt-dlp.archive` so rerunning changed playlists skips items already downloaded.
- Navidrome does not write metadata to music files. This downloader writes tagged files before Navidrome scans them.
