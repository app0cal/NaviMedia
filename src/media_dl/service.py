"""Serve the localhost FastAPI API and bundled dashboard."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from media_dl import __version__
from media_dl.config import Config, load_config
from media_dl.db import Database, Job, Playlist, PlaylistItem
from media_dl.download_plan import (
    ALLOWED_METADATA_MODES,
    ALLOWED_OUTPUT_LAYOUTS,
    ALLOWED_PLAYLIST_MODES,
)
from media_dl.jobs import (
    ClearHistoryBlockedError,
    UnsupportedUrlError,
    add_url,
    clear_history,
    import_queue_files,
    list_recent_jobs,
    retry_job,
    skip_job,
)
from media_dl.events import EventBroker
from media_dl.playlist import is_playlist_url
from media_dl.runtime_settings import (
    ALLOWED_AUDIO_FORMATS,
    ALLOWED_THUMBNAIL_MODES,
    load_runtime_settings,
    save_runtime_settings,
)
from media_dl.service_runtime import ServiceRuntime


WEB_DIST_DIR = Path(__file__).with_name("static")


class JobResponse(BaseModel):
    """Serialize a job row for API and dashboard consumers."""

    id: int
    source: str
    raw_url: str
    normalized_url: str
    status: str
    attempts: int
    last_error: str | None
    last_warning: str | None
    output_path: str | None
    dedupe_key: str
    duplicate_of: int | None
    allow_duplicate: bool
    parent_id: int | None
    child_count: int
    child_created_count: int
    child_duplicate_count: int
    child_error_count: int
    playlist_id: int | None
    media_item_id: int | None
    job_kind: str


class AddJobRequest(BaseModel):
    """Accept a URL submission with queueing and duplicate options."""

    url: str
    queue_only: bool = False
    allow_duplicate: bool = False
    track_playlist: bool = False
    interval_seconds: int = Field(default=604800, ge=3600, le=31536000)


class RetryJobRequest(BaseModel):
    """Accept retry options for a job action."""

    queue_only: bool = False


class SkipJobRequest(BaseModel):
    """Accept a manual skip reason."""

    reason: str = "manual skip"


class RunJobsRequest(BaseModel):
    """Accept a bounded manual run request."""

    max_jobs: int | None = Field(default=1, ge=1)


class AddJobResponse(BaseModel):
    """Return add-job state plus optional immediate processing result."""

    job: JobResponse
    created: bool
    processed: JobResponse | None
    accepted: bool = False


class JobActionResponse(BaseModel):
    """Return a changed job plus optional immediate processing result."""

    job: JobResponse
    processed: JobResponse | None = None
    accepted: bool = False


class JobListResponse(BaseModel):
    """Return one paginated job list response."""

    jobs: list[JobResponse]
    limit: int
    offset: int
    total: int
    has_more: bool


class RunJobsResponse(BaseModel):
    """Return how many staged jobs were accepted for background work."""

    queued: int


class ClearHistoryResponse(BaseModel):
    """Return clear-history counts and archive deletion state."""

    deleted_jobs: int
    archive_deleted: bool


class ImportQueueRowResponse(BaseModel):
    """Return one imported queue line result."""

    job: JobResponse | None
    created: bool
    error: str | None


class ImportQueueResponse(BaseModel):
    """Summarize an explicit queue file import."""

    imported: int
    duplicates: int
    errors: int
    rows: list[ImportQueueRowResponse]


class RuntimeSettingsResponse(BaseModel):
    """Return current runtime settings and allowed values for dashboard controls."""

    audio_format: str
    audio_formats: list[str]
    thumbnail_mode: str
    thumbnail_modes: list[str]
    output_layout: str
    output_layouts: list[str]
    metadata_mode: str
    metadata_modes: list[str]
    playlist_mode: str
    playlist_modes: list[str]
    default_thumbnail_path: str


class UpdateRuntimeSettingsRequest(BaseModel):
    """Accept a full or partially backward-compatible settings update."""

    audio_format: str
    thumbnail_mode: str = "source"
    output_layout: str | None = None
    metadata_mode: str | None = None
    playlist_mode: str | None = None


class PlaylistResponse(BaseModel):
    """Serialize playlist inventory and tracking state."""

    id: int
    source: str
    raw_url: str
    normalized_url: str
    title: str | None
    job_id: int | None
    tracked: bool
    paused: bool
    interval_seconds: int
    last_checked_at: str | None
    last_success_at: str | None
    next_check_at: str | None
    last_error: str | None


class PlaylistItemResponse(BaseModel):
    """Serialize one playlist membership and canonical download state."""

    id: int
    playlist_id: int
    source: str
    provider_id: str
    url: str
    title: str
    artist: str
    position: int
    active: bool
    download_status: str
    output_path: str | None
    last_error: str | None
    download_job_id: int | None


class PlaylistListResponse(BaseModel):
    """Return tracked playlists."""

    playlists: list[PlaylistResponse]


class PlaylistItemsResponse(BaseModel):
    """Return one paginated playlist item page."""

    items: list[PlaylistItemResponse]
    limit: int
    offset: int
    total: int
    has_more: bool


class UpdatePlaylistRequest(BaseModel):
    """Accept schedule and tracking control changes."""

    tracked: bool | None = None
    paused: bool | None = None
    interval_seconds: int | None = Field(default=None, ge=3600, le=31536000)


def create_app(config: Config | None = None) -> FastAPI:
    """Create the FastAPI app with API routes and static dashboard fallback."""
    cfg = config or load_config()
    broker = EventBroker()
    runtime_worker = ServiceRuntime(cfg, broker)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        """Start and stop the single background runtime with the API service."""
        cfg.ensure_dirs()
        runtime_worker.start()
        try:
            yield
        finally:
            runtime_worker.stop()

    app = FastAPI(
        title="Navidrome Media Downloader",
        version=__version__,
        lifespan=lifespan,
    )
    app.state.runtime = runtime_worker
    app.state.events = broker
    assets_dir = WEB_DIST_DIR / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        """Return a minimal health response for smoke checks."""
        return {
            "status": "ok",
            "version": __version__,
        }

    @app.get("/api/runtime")
    def runtime() -> dict[str, Any]:
        """Return resolved paths and active runtime settings."""
        current = load_runtime_settings(cfg)
        return {
            "music_root": str(cfg.music_root),
            "state_dir": str(cfg.state_dir),
            "queue_dir": str(cfg.queue_dir),
            "download_dir": str(cfg.download_dir),
            "config_dir": str(cfg.resolved_config_dir),
            "default_thumbnail_path": str(cfg.resolved_default_thumbnail_path),
            "audio_format": current.audio_format,
            "thumbnail_mode": current.thumbnail_mode,
            "output_layout": current.output_layout,
            "metadata_mode": current.metadata_mode,
            "playlist_mode": current.playlist_mode,
            "service_port": cfg.service_port,
        }

    @app.get("/api/settings", response_model=RuntimeSettingsResponse)
    def settings() -> RuntimeSettingsResponse:
        """Return persisted settings and allowed option lists."""
        current = load_runtime_settings(cfg)
        return RuntimeSettingsResponse(
            audio_format=current.audio_format,
            audio_formats=list(ALLOWED_AUDIO_FORMATS),
            thumbnail_mode=current.thumbnail_mode,
            thumbnail_modes=list(ALLOWED_THUMBNAIL_MODES),
            output_layout=current.output_layout,
            output_layouts=list(ALLOWED_OUTPUT_LAYOUTS),
            metadata_mode=current.metadata_mode,
            metadata_modes=list(ALLOWED_METADATA_MODES),
            playlist_mode=current.playlist_mode,
            playlist_modes=list(ALLOWED_PLAYLIST_MODES),
            default_thumbnail_path=str(cfg.resolved_default_thumbnail_path),
        )

    @app.put("/api/settings", response_model=RuntimeSettingsResponse)
    def update_settings(request: UpdateRuntimeSettingsRequest) -> RuntimeSettingsResponse:
        """Persist validated runtime settings."""
        existing = load_runtime_settings(cfg)
        try:
            current = save_runtime_settings(
                cfg,
                request.audio_format,
                thumbnail_mode=request.thumbnail_mode,
                output_layout=request.output_layout or existing.output_layout,
                metadata_mode=request.metadata_mode or existing.metadata_mode,
                playlist_mode=request.playlist_mode or existing.playlist_mode,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return RuntimeSettingsResponse(
            audio_format=current.audio_format,
            audio_formats=list(ALLOWED_AUDIO_FORMATS),
            thumbnail_mode=current.thumbnail_mode,
            thumbnail_modes=list(ALLOWED_THUMBNAIL_MODES),
            output_layout=current.output_layout,
            output_layouts=list(ALLOWED_OUTPUT_LAYOUTS),
            metadata_mode=current.metadata_mode,
            metadata_modes=list(ALLOWED_METADATA_MODES),
            playlist_mode=current.playlist_mode,
            playlist_modes=list(ALLOWED_PLAYLIST_MODES),
            default_thumbnail_path=str(cfg.resolved_default_thumbnail_path),
        )

    @app.get("/api/jobs", response_model=JobListResponse)
    def jobs(
        limit: int = Query(default=25, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
    ) -> JobListResponse:
        """Return a bounded newest-first page of jobs."""
        result = list_recent_jobs(cfg, limit=limit, offset=offset)
        return JobListResponse(
            jobs=[_job_response(job) for job in result.jobs],
            limit=result.limit,
            offset=result.offset,
            total=result.total,
            has_more=result.has_more,
        )

    @app.post("/api/jobs", response_model=AddJobResponse)
    def create_job(request: AddJobRequest) -> AddJobResponse:
        """Create or refresh a job and optionally queue background processing."""
        if request.track_playlist and request.queue_only:
            raise HTTPException(
                status_code=400,
                detail="tracked playlists cannot be submitted as queue only",
            )
        if request.track_playlist and request.allow_duplicate:
            raise HTTPException(
                status_code=400,
                detail="tracked playlists cannot be forced duplicates",
            )
        if request.track_playlist and not is_playlist_url(request.url):
            raise HTTPException(
                status_code=400,
                detail="tracking is only available for playlist URLs",
            )
        if request.interval_seconds % 3600:
            raise HTTPException(
                status_code=400,
                detail="playlist intervals must use whole hours",
            )
        try:
            result = add_url(
                cfg,
                request.url,
                queue_only=True,
                allow_duplicate=request.allow_duplicate,
            )
        except UnsupportedUrlError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        if is_playlist_url(result.job.normalized_url):
            db = Database(cfg.db_path)
            try:
                db.ensure_playlist(
                    result.job,
                    tracked=request.track_playlist,
                    interval_seconds=request.interval_seconds,
                )
                if request.track_playlist and result.job.status not in {"queued", "retry"}:
                    db.retry(result.job.id)
            finally:
                db.close()

        accepted = False
        refreshed = _get_job(cfg, result.job.id)
        if not request.queue_only and refreshed.status in {"queued", "retry"}:
            accepted = runtime_worker.enqueue_job(result.job.id)

        return AddJobResponse(
            job=_job_response(refreshed),
            created=result.created,
            processed=None,
            accepted=accepted,
        )

    @app.post("/api/jobs/{job_id}/retry", response_model=JobActionResponse)
    def retry(job_id: int, request: RetryJobRequest) -> JobActionResponse:
        """Mark a job for retry and optionally queue it for background work."""
        try:
            result = retry_job(cfg, job_id, queue_only=True)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        accepted = False
        if not request.queue_only:
            accepted = runtime_worker.enqueue_job(job_id)
        return JobActionResponse(
            job=_job_response(result.job),
            processed=None,
            accepted=accepted,
        )

    @app.post("/api/jobs/{job_id}/skip", response_model=JobActionResponse)
    def skip(job_id: int, request: SkipJobRequest) -> JobActionResponse:
        """Mark a job skipped with a manual reason."""
        try:
            job = skip_job(cfg, job_id, request.reason)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        return JobActionResponse(job=_job_response(job))

    @app.post("/api/run", response_model=RunJobsResponse)
    def run_jobs(request: RunJobsRequest) -> RunJobsResponse:
        """Queue a bounded number of staged jobs for background processing."""
        return RunJobsResponse(
            queued=runtime_worker.enqueue_queued_jobs(request.max_jobs)
        )

    @app.post("/api/jobs/clear-history", response_model=ClearHistoryResponse)
    def clear_job_history() -> ClearHistoryResponse:
        """Clear job history and the YouTube archive unless a job is running."""
        try:
            result = clear_history(cfg)
        except ClearHistoryBlockedError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return ClearHistoryResponse(
            deleted_jobs=result.deleted_jobs,
            archive_deleted=result.archive_deleted,
        )

    @app.post("/api/import-queue", response_model=ImportQueueResponse)
    def import_queue() -> ImportQueueResponse:
        """Import standard queue files into jobs without processing them."""
        return _queue_import_response(import_queue_files(cfg))

    @app.get("/api/playlists/tracked", response_model=PlaylistListResponse)
    def tracked_playlists() -> PlaylistListResponse:
        """Return pinned tracked-playlist definitions."""
        db = Database(cfg.db_path)
        try:
            playlists = db.list_tracked_playlists()
        finally:
            db.close()
        return PlaylistListResponse(
            playlists=[_playlist_response(playlist) for playlist in playlists]
        )

    @app.get(
        "/api/playlists/{playlist_id}/items",
        response_model=PlaylistItemsResponse,
    )
    def playlist_items(
        playlist_id: int,
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
    ) -> PlaylistItemsResponse:
        """Return ordered current and removed membership for one playlist."""
        db = Database(cfg.db_path)
        try:
            try:
                items, total = db.list_playlist_items(
                    playlist_id, limit=limit, offset=offset
                )
            except KeyError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
        finally:
            db.close()
        return PlaylistItemsResponse(
            items=[_playlist_item_response(item) for item in items],
            limit=limit,
            offset=offset,
            total=total,
            has_more=offset + len(items) < total,
        )

    @app.patch("/api/playlists/{playlist_id}", response_model=PlaylistResponse)
    def update_playlist(
        playlist_id: int,
        request: UpdatePlaylistRequest,
    ) -> PlaylistResponse:
        """Update a playlist schedule, pause state, or tracking state."""
        if (
            request.interval_seconds is not None
            and request.interval_seconds % 3600
        ):
            raise HTTPException(
                status_code=400,
                detail="playlist intervals must use whole hours",
            )
        db = Database(cfg.db_path)
        try:
            try:
                playlist = db.update_playlist_tracking(
                    playlist_id,
                    tracked=request.tracked,
                    paused=request.paused,
                    interval_seconds=request.interval_seconds,
                )
            except KeyError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
        finally:
            db.close()
        broker.publish("playlist", playlist_id=playlist_id)
        if playlist.tracked and not playlist.paused and playlist.next_check_at:
            runtime_worker.wake()
        return _playlist_response(playlist)

    @app.post("/api/playlists/{playlist_id}/check", response_model=JobActionResponse)
    def check_playlist(playlist_id: int) -> JobActionResponse:
        """Queue an immediate playlist inventory check."""
        try:
            job_id, accepted = runtime_worker.queue_playlist_check(playlist_id)
            job = _get_job(cfg, job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return JobActionResponse(
            job=_job_response(job),
            processed=None,
            accepted=accepted,
        )

    @app.get("/api/events")
    def events() -> StreamingResponse:
        """Stream change notifications for dashboard REST refreshes."""
        return StreamingResponse(
            broker.stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    @app.get("/")
    def web_index() -> FileResponse:
        """Serve the dashboard entrypoint."""
        return _web_index_response()

    @app.get("/{full_path:path}")
    def web_fallback(full_path: str) -> FileResponse:
        """Serve the dashboard for non-API paths so client routing can work."""
        if full_path == "api" or full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not Found")
        return _web_index_response()

    return app


def serve(config: Config | None = None) -> None:
    """Run Uvicorn for the localhost service."""
    cfg = config or load_config()
    uvicorn.run(
        create_app(cfg),
        host="0.0.0.0",
        port=cfg.service_port,
        log_level="info",
    )


@contextmanager
def app_for_tests(config: Config) -> Iterator[FastAPI]:
    """Yield an app instance for FastAPI TestClient tests."""
    yield create_app(config)


def _job_response(job: Job) -> JobResponse:
    """Convert a Job dataclass into its API response model."""
    return JobResponse(
        id=job.id,
        source=job.source.value,
        raw_url=job.raw_url,
        normalized_url=job.normalized_url,
        status=job.status,
        attempts=job.attempts,
        last_error=job.last_error,
        last_warning=job.last_warning,
        output_path=job.output_path,
        dedupe_key=job.dedupe_key,
        duplicate_of=job.duplicate_of,
        allow_duplicate=job.allow_duplicate,
        parent_id=job.parent_id,
        child_count=job.child_count,
        child_created_count=job.child_created_count,
        child_duplicate_count=job.child_duplicate_count,
        child_error_count=job.child_error_count,
        playlist_id=job.playlist_id,
        media_item_id=job.media_item_id,
        job_kind=job.job_kind,
    )


def _playlist_response(playlist: Playlist) -> PlaylistResponse:
    """Convert a Playlist dataclass into its API response model."""
    return PlaylistResponse(
        id=playlist.id,
        source=playlist.source.value,
        raw_url=playlist.raw_url,
        normalized_url=playlist.normalized_url,
        title=playlist.title,
        job_id=playlist.job_id,
        tracked=playlist.tracked,
        paused=playlist.paused,
        interval_seconds=playlist.interval_seconds,
        last_checked_at=playlist.last_checked_at,
        last_success_at=playlist.last_success_at,
        next_check_at=playlist.next_check_at,
        last_error=playlist.last_error,
    )


def _playlist_item_response(item: PlaylistItem) -> PlaylistItemResponse:
    """Convert a PlaylistItem dataclass into its API response model."""
    return PlaylistItemResponse(
        id=item.id,
        playlist_id=item.playlist_id,
        source=item.source.value,
        provider_id=item.provider_id,
        url=item.url,
        title=item.title,
        artist=item.artist,
        position=item.position,
        active=item.active,
        download_status=item.download_status,
        output_path=item.output_path,
        last_error=item.last_error,
        download_job_id=item.download_job_id,
    )


def _get_job(config: Config, job_id: int) -> Job:
    """Fetch one job through a short-lived service database connection."""
    db = Database(config.db_path)
    try:
        return db.get_job(job_id)
    finally:
        db.close()


def _queue_import_response(
    results: list[tuple[Job | None, bool, str | None]],
) -> ImportQueueResponse:
    """Convert queue import tuples into API summary and row models."""
    rows = [
        ImportQueueRowResponse(
            job=_job_response(job) if job else None,
            created=created,
            error=error,
        )
        for job, created, error in results
    ]
    return ImportQueueResponse(
        imported=sum(1 for job, created, error in results if job and created and not error),
        duplicates=sum(1 for job, created, error in results if job and not created and not error),
        errors=sum(1 for _job, _created, error in results if error),
        rows=rows,
    )


def _web_index_response() -> FileResponse:
    """Return the built dashboard index file or a clear 404 when missing."""
    index_path = WEB_DIST_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=404, detail="web UI not built")
    return FileResponse(index_path)
