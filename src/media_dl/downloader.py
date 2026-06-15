"""Build and run yt-dlp/spotDL commands, then apply local post-processing."""

from __future__ import annotations

import base64
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from media_dl.config import Config
from media_dl.db import Job
from media_dl.download_plan import (
    DEFAULT_METADATA_MODE,
    DEFAULT_OUTPUT_LAYOUT,
    DEFAULT_PLAYLIST_MODE,
    DownloadPlan,
    DownloadSettings,
    plan_download,
)
from media_dl.metadata import clean_new_audio_metadata
from media_dl.urltools import Source


AUDIO_SUFFIXES = {".flac", ".m4a", ".mp3", ".ogg", ".opus", ".wav"}
IMAGE_MIME_BY_SUFFIX = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
}


@dataclass(frozen=True)
class DownloadResult:
    """Return the output root and any non-fatal warning from a completed download."""

    output_path: Path
    warning: str | None = None


@dataclass(frozen=True)
class _ConfigSettings:
    """Adapt static Config defaults to the runtime settings protocol."""

    audio_format: str
    thumbnail_mode: str
    output_layout: str = DEFAULT_OUTPUT_LAYOUT
    metadata_mode: str = DEFAULT_METADATA_MODE
    playlist_mode: str = DEFAULT_PLAYLIST_MODE


class Downloader:
    """Coordinate command generation, execution, artwork, and metadata cleanup."""

    def __init__(self, config: Config, settings: DownloadSettings | None = None):
        """Store config and runtime settings for future commands."""
        self.config = config
        self.settings = settings or _ConfigSettings(
            audio_format=config.audio_format,
            thumbnail_mode=config.thumbnail_mode,
        )

    def command_for(self, job: Job) -> list[str]:
        """Build a downloader command directly from a job."""
        return self.command_for_plan(plan_download(self.config, job, self.settings))

    def command_for_plan(self, plan: DownloadPlan) -> list[str]:
        """Dispatch a download plan to the source-specific command builder."""
        if plan.source == Source.YOUTUBE:
            return self.youtube_command_for_plan(plan)
        if plan.source == Source.SPOTIFY:
            return self.spotify_command_for_plan(plan)
        raise ValueError(f"unsupported source: {plan.source}")

    def run(self, job: Job) -> DownloadResult:
        """Run the configured downloader and post-process newly created audio files."""
        plan = plan_download(self.config, job, self.settings)
        command = self.command_for_plan(plan)
        started_at = time.time()
        completed = subprocess.run(
            command,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        warning = _warning_from_output(completed.stdout)
        if completed.returncode != 0:
            if warning and _finished_playlist(completed.stdout):
                output_root = plan.output_layout.output_root
                new_audio_files = _new_audio_files(output_root, started_at)
                return DownloadResult(
                    output_path=output_root,
                    warning=_combine_warnings(
                        warning,
                        self._apply_default_thumbnail(new_audio_files, plan.thumbnail_mode),
                        clean_new_audio_metadata(new_audio_files, plan.metadata_policy),
                    ),
                )
            raise RuntimeError(completed.stdout.strip() or f"command failed: {command[0]}")

        output_root = plan.output_layout.output_root
        new_audio_files = _new_audio_files(output_root, started_at)
        return DownloadResult(
            output_path=output_root,
            warning=_combine_warnings(
                warning,
                self._apply_default_thumbnail(new_audio_files, plan.thumbnail_mode),
                clean_new_audio_metadata(new_audio_files, plan.metadata_policy),
            ),
        )

    def youtube_command(self, url: str, duplicate_job_id: int | None = None) -> list[str]:
        """Build an ad-hoc YouTube command for tests and CLI-style callers."""
        plan = self._ad_hoc_plan(
            source=Source.YOUTUBE,
            url=url,
            duplicate_job_id=duplicate_job_id,
        )
        return self.youtube_command_for_plan(plan)

    def youtube_command_for_plan(self, plan: DownloadPlan) -> list[str]:
        """Build the yt-dlp command for a YouTube download plan."""
        command = [
            self.config.yt_dlp_bin,
            "--ignore-errors",
            "--yes-playlist",
            "--extract-audio",
            "--audio-format",
            plan.audio_format,
            "--embed-metadata",
            "--js-runtimes",
            "deno",
        ]
        if plan.thumbnail_mode == "source":
            command.append("--embed-thumbnail")
        if plan.use_archive:
            command.extend(
                [
                    "--download-archive",
                    str(self.config.yt_archive_path),
                ]
            )
        command.extend(
            [
            "--paths",
            f"temp:{self.config.tmp_dir}",
            "--output",
            plan.output_layout.output_template,
            plan.url,
            ]
        )
        return command

    def spotify_command(self, url: str, duplicate_job_id: int | None = None) -> list[str]:
        """Build an ad-hoc Spotify command for tests and CLI-style callers."""
        plan = self._ad_hoc_plan(
            source=Source.SPOTIFY,
            url=url,
            duplicate_job_id=duplicate_job_id,
        )
        return self.spotify_command_for_plan(plan)

    def spotify_command_for_plan(self, plan: DownloadPlan) -> list[str]:
        """Build the spotDL command for a Spotify download plan."""
        command = [
            self.config.spotdl_bin,
            "download",
            plan.url,
            "--format",
            plan.audio_format,
            "--bitrate",
            "disable",
            "--output",
            plan.output_layout.output_template,
        ]
        if plan.thumbnail_mode != "source":
            command.append("--skip-album-art")
        return command

    def output_root_for(self, source: Source, duplicate_job_id: int | None = None) -> Path:
        """Return the output root that would be used for a source and duplicate state."""
        return self._ad_hoc_plan(
            source=source,
            url="",
            duplicate_job_id=duplicate_job_id,
        ).output_layout.output_root

    def _apply_default_thumbnail(self, paths: list[Path], thumbnail_mode: str) -> str | None:
        """Embed configured default artwork into new files when requested."""
        if thumbnail_mode != "default":
            return None
        thumbnail_path = self.config.resolved_default_thumbnail_path
        if not thumbnail_path.is_file():
            return f"default thumbnail not found: {thumbnail_path}"

        try:
            image_bytes = thumbnail_path.read_bytes()
        except OSError as exc:
            return f"default thumbnail could not be read: {exc}"

        mime = IMAGE_MIME_BY_SUFFIX.get(thumbnail_path.suffix.lower())
        if mime is None:
            return f"default thumbnail must be a jpg or png file: {thumbnail_path}"

        failures: list[str] = []
        for path in paths:
            try:
                _embed_thumbnail(path, image_bytes, mime)
            except Exception as exc:
                failures.append(f"{path.name}: {exc}")

        if failures:
            return f"default thumbnail failed for {len(failures)} file(s): {failures[0]}"
        return None

    def _ad_hoc_plan(
        self,
        source: Source,
        url: str,
        duplicate_job_id: int | None,
    ) -> DownloadPlan:
        """Create a temporary job to reuse the normal planning path."""
        job = Job(
            id=duplicate_job_id or 0,
            source=source,
            raw_url=url,
            normalized_url=url,
            status="queued",
            attempts=0,
            last_error=None,
            last_warning=None,
            output_path=None,
            dedupe_key=url,
            duplicate_of=None,
            allow_duplicate=duplicate_job_id is not None,
        )
        return plan_download(self.config, job, self.settings)


def _warning_from_output(output: str) -> str | None:
    """Extract a playlist warning from downloader output that contains errors."""
    errors = [line.strip() for line in output.splitlines() if line.strip().startswith("ERROR:")]
    if not errors:
        return None
    first = errors[0]
    suffix = f" First error: {first}" if first else ""
    return f"Some playlist items failed.{suffix}"


def _finished_playlist(output: str) -> bool:
    """Return whether yt-dlp reported that playlist processing finished."""
    return any(
        line.strip().startswith("[download] Finished downloading playlist:")
        for line in output.splitlines()
    )


def _combine_warnings(*warnings: str | None) -> str | None:
    """Join non-empty warning strings into a single job warning."""
    present = [warning for warning in warnings if warning]
    if not present:
        return None
    return " ".join(present)


def _new_audio_files(output_root: Path, started_at: float) -> list[Path]:
    """Find audio files under the output root that were modified by this run."""
    if not output_root.exists():
        return []
    files: list[Path] = []
    for path in output_root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in AUDIO_SUFFIXES:
            continue
        try:
            if path.stat().st_mtime + 1 < started_at:
                continue
        except OSError:
            continue
        files.append(path)
    return files


def _embed_thumbnail(path: Path, image_bytes: bytes, mime: str) -> None:
    """Embed cover art into supported audio containers using mutagen."""
    suffix = path.suffix.lower()
    if suffix == ".mp3":
        from mutagen.id3 import APIC
        from mutagen.mp3 import MP3

        audio = MP3(path)
        if audio.tags is None:
            audio.add_tags()
        audio.tags.delall("APIC")
        audio.tags.add(APIC(encoding=3, mime=mime, type=3, desc="Cover", data=image_bytes))
        audio.save()
        return

    if suffix == ".m4a":
        from mutagen.mp4 import MP4, MP4Cover

        image_format = MP4Cover.FORMAT_PNG if mime == "image/png" else MP4Cover.FORMAT_JPEG
        audio = MP4(path)
        audio["covr"] = [MP4Cover(image_bytes, imageformat=image_format)]
        audio.save()
        return

    if suffix == ".flac":
        from mutagen.flac import FLAC, Picture

        audio = FLAC(path)
        audio.clear_pictures()
        audio.add_picture(_picture(image_bytes, mime))
        audio.save()
        return

    if suffix == ".opus":
        from mutagen.oggopus import OggOpus

        audio = OggOpus(path)
        encoded = base64.b64encode(_picture(image_bytes, mime).write()).decode("ascii")
        audio["metadata_block_picture"] = [encoded]
        audio.save()
        return

    if suffix == ".ogg":
        from mutagen.oggvorbis import OggVorbis

        audio = OggVorbis(path)
        encoded = base64.b64encode(_picture(image_bytes, mime).write()).decode("ascii")
        audio["metadata_block_picture"] = [encoded]
        audio.save()
        return

    raise ValueError(f"unsupported audio format for default thumbnail: {suffix}")


def _picture(image_bytes: bytes, mime: str):
    """Build a FLAC-style picture block for formats that share that representation."""
    from mutagen.flac import Picture

    picture = Picture()
    picture.type = 3
    picture.mime = mime
    picture.data = image_bytes
    return picture
