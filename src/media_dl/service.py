from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from media_dl import __version__
from media_dl.config import Config, load_config
from media_dl.db import Job
from media_dl.jobs import (
    UnsupportedUrlError,
    add_url,
    import_queue_files,
    list_recent_jobs,
    retry_job,
    run_queued_jobs,
    skip_job,
)
from media_dl.runtime_settings import (
    ALLOWED_AUDIO_FORMATS,
    load_runtime_settings,
    save_runtime_settings,
)


WEB_DIST_DIR = Path(__file__).with_name("static")


class JobResponse(BaseModel):
    id: int
    source: str
    raw_url: str
    normalized_url: str
    status: str
    attempts: int
    last_error: str | None
    output_path: str | None
    dedupe_key: str
    duplicate_of: int | None
    allow_duplicate: bool


class AddJobRequest(BaseModel):
    url: str
    queue_only: bool = False
    allow_duplicate: bool = False


class RetryJobRequest(BaseModel):
    queue_only: bool = False


class SkipJobRequest(BaseModel):
    reason: str = "manual skip"


class RunJobsRequest(BaseModel):
    max_jobs: int | None = Field(default=1, ge=1)


class AddJobResponse(BaseModel):
    job: JobResponse
    created: bool
    processed: JobResponse | None


class JobActionResponse(BaseModel):
    job: JobResponse
    processed: JobResponse | None = None


class JobListResponse(BaseModel):
    jobs: list[JobResponse]


class RunJobsResponse(BaseModel):
    processed: int


class ImportQueueRowResponse(BaseModel):
    job: JobResponse | None
    created: bool
    error: str | None


class ImportQueueResponse(BaseModel):
    imported: int
    duplicates: int
    errors: int
    rows: list[ImportQueueRowResponse]


class RuntimeSettingsResponse(BaseModel):
    audio_format: str
    audio_formats: list[str]


class UpdateRuntimeSettingsRequest(BaseModel):
    audio_format: str


def create_app(config: Config | None = None) -> FastAPI:
    cfg = config or load_config()
    app = FastAPI(title="Navidrome Media Downloader", version=__version__)
    assets_dir = WEB_DIST_DIR / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "version": __version__,
        }

    @app.get("/api/runtime")
    def runtime() -> dict[str, Any]:
        return {
            "music_root": str(cfg.music_root),
            "state_dir": str(cfg.state_dir),
            "queue_dir": str(cfg.queue_dir),
            "download_dir": str(cfg.download_dir),
            "audio_format": load_runtime_settings(cfg).audio_format,
            "service_port": cfg.service_port,
        }

    @app.get("/api/settings", response_model=RuntimeSettingsResponse)
    def settings() -> RuntimeSettingsResponse:
        current = load_runtime_settings(cfg)
        return RuntimeSettingsResponse(
            audio_format=current.audio_format,
            audio_formats=list(ALLOWED_AUDIO_FORMATS),
        )

    @app.put("/api/settings", response_model=RuntimeSettingsResponse)
    def update_settings(request: UpdateRuntimeSettingsRequest) -> RuntimeSettingsResponse:
        try:
            current = save_runtime_settings(cfg, request.audio_format)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return RuntimeSettingsResponse(
            audio_format=current.audio_format,
            audio_formats=list(ALLOWED_AUDIO_FORMATS),
        )

    @app.get("/api/jobs", response_model=JobListResponse)
    def jobs(limit: int = Query(default=50, ge=1, le=500)) -> JobListResponse:
        return JobListResponse(
            jobs=[_job_response(job) for job in list_recent_jobs(cfg, limit=limit)]
        )

    @app.post("/api/jobs", response_model=AddJobResponse)
    def create_job(request: AddJobRequest) -> AddJobResponse:
        try:
            result = add_url(
                cfg,
                request.url,
                queue_only=request.queue_only,
                allow_duplicate=request.allow_duplicate,
            )
        except UnsupportedUrlError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        return AddJobResponse(
            job=_job_response(result.job),
            created=result.created,
            processed=_job_response(result.processed) if result.processed else None,
        )

    @app.post("/api/jobs/{job_id}/retry", response_model=JobActionResponse)
    def retry(job_id: int, request: RetryJobRequest) -> JobActionResponse:
        try:
            result = retry_job(cfg, job_id, queue_only=request.queue_only)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        return JobActionResponse(
            job=_job_response(result.job),
            processed=_job_response(result.processed) if result.processed else None,
        )

    @app.post("/api/jobs/{job_id}/skip", response_model=JobActionResponse)
    def skip(job_id: int, request: SkipJobRequest) -> JobActionResponse:
        try:
            job = skip_job(cfg, job_id, request.reason)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        return JobActionResponse(job=_job_response(job))

    @app.post("/api/run", response_model=RunJobsResponse)
    def run_jobs(request: RunJobsRequest) -> RunJobsResponse:
        return RunJobsResponse(processed=run_queued_jobs(cfg, max_jobs=request.max_jobs))

    @app.post("/api/import-queue", response_model=ImportQueueResponse)
    def import_queue() -> ImportQueueResponse:
        return _queue_import_response(import_queue_files(cfg))

    @app.get("/")
    def web_index() -> FileResponse:
        return _web_index_response()

    @app.get("/{full_path:path}")
    def web_fallback(full_path: str) -> FileResponse:
        if full_path == "api" or full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not Found")
        return _web_index_response()

    @app.on_event("startup")
    def startup() -> None:
        cfg.ensure_dirs()

    return app


def serve(config: Config | None = None) -> None:
    cfg = config or load_config()
    uvicorn.run(
        create_app(cfg),
        host="0.0.0.0",
        port=cfg.service_port,
        log_level="info",
    )


@contextmanager
def app_for_tests(config: Config) -> Iterator[FastAPI]:
    yield create_app(config)


def _job_response(job: Job) -> JobResponse:
    return JobResponse(
        id=job.id,
        source=job.source.value,
        raw_url=job.raw_url,
        normalized_url=job.normalized_url,
        status=job.status,
        attempts=job.attempts,
        last_error=job.last_error,
        output_path=job.output_path,
        dedupe_key=job.dedupe_key,
        duplicate_of=job.duplicate_of,
        allow_duplicate=job.allow_duplicate,
    )


def _queue_import_response(
    results: list[tuple[Job | None, bool, str | None]],
) -> ImportQueueResponse:
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
    index_path = WEB_DIST_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=404, detail="web UI not built")
    return FileResponse(index_path)
