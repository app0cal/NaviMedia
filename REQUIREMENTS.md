# Playlist Tracking Integration

This document is the implementation roadmap and acceptance contract for playlist
inventory, recurring tracking, and live dashboard updates. Complete and verify each
phase before starting the next one.

## Fixed Boundaries

- Support public and unlisted YouTube, YouTube Music, and Spotify playlists that the
  existing provider tools can access without new credentials.
- Keep the service single-user, localhost-only, and limited to one download worker.
- Never delete local media when a song is removed from a source playlist.
- Add tracking controls to the dashboard and API only. Do not add tracking CLI commands.
- Use server-sent events (SSE) for live dashboard updates. Inbound and outbound
  automation webhooks are out of scope.

## Phase 1: Playlist Inventory and Song Details

### SQLite foundation

- [x] Add persistent playlist, canonical media-item, and playlist-membership records.
- [x] Extend jobs with playlist/media identity and a job kind.
- [x] Migrate every database shape supported by the current application without losing
      existing job history.
- [x] Allow one canonical media item to appear in multiple playlists without duplicating
      its download.

### Provider inventory

- [x] Detect playlist URLs separately from individual media URLs.
- [x] Inventory YouTube playlists with flat `yt-dlp` JSON.
- [x] Inventory Spotify playlists with `spotdl save <url> --save-file -`.
- [x] Normalize provider ID, URL, title, artist, and position.
- [x] Reconcile successful inventories atomically, marking missing entries removed and
      reactivating returning entries without deleting files.
- [x] Preserve the last successful membership if provider extraction fails.

### API and dashboard

- [x] Inventory every submitted playlist, whether tracked or one-time.
- [x] Expose paginated playlist items through the API.
- [x] Add playlist identity and inventory state to job responses.
- [x] Add an expandable item list to playlist job rows with title, artist, position,
      membership state, download state, and errors.
- [x] For single-job downloads, show the truthful overall batch result rather than
      claiming exact per-item success.

### Phase 1 verification

- [x] Test migrations, parsing, reconciliation, shared items, removal/reappearance,
      failure preservation, pagination, and response serialization.
- [x] Run the complete Python test suite and the frontend production build.
- [x] Verify expandable playlist rows at desktop and mobile widths.

## Phase 2: Tracking, Scheduler, and Background Work

### Persistent work queue

- [x] Add persistent work records for playlist checks and downloads.
- [x] Prevent duplicate pending/running work for the same operation and target.
- [x] Recover interrupted work after restart.
- [x] Start one service-owned worker through the FastAPI lifespan and continue using the
      existing filesystem download lock.
- [x] Leave queue-only jobs idle until an explicit manual run.

### Asynchronous execution

- [x] Make service submissions, retries, manual runs, and playlist checks enqueue work
      and return promptly.
- [x] Keep container-internal CLI execution synchronous.
- [x] Update host CLI output to report queued API work without adding tracking commands.

### Tracked playlist scheduling

- [x] Add tracking state, pause state, interval, last-check, last-success, next-check,
      and latest-error fields.
- [x] Add a submit-time Track playlist toggle that is valid only for playlists,
      incompatible with Queue only, weekly by default, and queues the initial inventory.
- [x] Support daily, weekly, 30-day monthly, and custom whole-hour/day intervals from
      one hour through 365 days. Store schedule timestamps in UTC.
- [x] Always use item-level jobs for tracked playlists, regardless of the global
      playlist handling setting.
- [x] On each check, queue newly discovered and failed items while skipping successful
      items.
- [x] Calculate the next run from check completion. Run now resets that baseline.
- [x] Queue one catch-up check for each overdue playlist after restart.
- [x] Support Run now, Pause/Resume, schedule editing, and Stop tracking.
- [x] Preserve tracked definitions, schedules, current membership, and media state when
      clearing job history.

### Tracking API and dashboard

- [x] Extend job submission with tracking options.
- [x] Expose tracked-playlist listing, editing, and Run now endpoints.
- [x] Add a pinned Tracked playlists section above paginated job history.

### Phase 2 verification

- [x] Test queue ordering, duplicate prevention, recovery, and single-worker behavior.
- [x] Test initial and incremental downloads, retry behavior, shared-item dedupe, and
      removed-item retention.
- [x] Test schedule calculations, pause/resume, Run now, overdue recovery, validation,
      endpoints, and clear-history preservation.
- [x] Run the complete Python suite, frontend build, Docker build, and restart smoke test.

## Phase 3: Live Updates Through SSE

### Backend event stream

- [x] Publish job, playlist, and media-item change notifications in-process.
- [x] Expose `GET /api/events` as an SSE stream with compact record identifiers and
      periodic heartbeats.
- [x] Clean up disconnected subscribers and never block worker execution on an SSE
      client.
- [x] Keep REST endpoints authoritative; SSE only signals when to reload them.

### Dashboard integration

- [x] Open one `EventSource` connection.
- [x] Debounce notifications and reload only affected data.
- [x] Perform a full REST refresh after connecting or reconnecting.
- [x] Retain the explicit Refresh action and usable behavior while SSE is unavailable.

### Phase 3 verification

- [x] Test event delivery, heartbeats, subscriber cleanup, and reconnect recovery.
- [x] Verify downloads and scheduled checks never wait on SSE clients.
- [x] Run the complete Python suite and frontend production build.
- [x] Smoke-test live updates during a complete playlist inventory and download flow.

## Final Integration

- [ ] Validate one YouTube and one Spotify tracked playlist through initial download,
      incremental update, pause/resume, restart recovery, and live UI updates.
- [x] Update README and API documentation with interval behavior, playlist visibility
      limits, retained removed files, asynchronous responses, clear-history behavior,
      and SSE reconnection.
- [x] Run the complete Python suite, frontend build, Docker image build, and Compose
      startup checks.
