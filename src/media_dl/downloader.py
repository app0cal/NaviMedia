from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from media_dl.config import Config
from media_dl.db import Job
from media_dl.urltools import Source


@dataclass(frozen=True)
class DownloadResult:
    output_path: Path


class Downloader:
    def __init__(self, config: Config):
        self.config = config

    def command_for(self, job: Job) -> list[str]:
        if job.source == Source.YOUTUBE:
            return self.youtube_command(
                job.normalized_url,
                duplicate_job_id=job.id if job.allow_duplicate else None,
            )
        if job.source == Source.SPOTIFY:
            return self.spotify_command(
                job.normalized_url,
                duplicate_job_id=job.id if job.allow_duplicate else None,
            )
        raise ValueError(f"unsupported source: {job.source}")

    def run(self, job: Job) -> DownloadResult:
        command = self.command_for(job)
        completed = subprocess.run(
            command,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        if completed.returncode != 0:
            raise RuntimeError(completed.stdout.strip() or f"command failed: {command[0]}")

        return DownloadResult(
            output_path=self.output_root_for(
                job.source,
                duplicate_job_id=job.id if job.allow_duplicate else None,
            )
        )

    def youtube_command(self, url: str, duplicate_job_id: int | None = None) -> list[str]:
        output_root = (
            self.config.music_root / "Duplicates" / str(duplicate_job_id) / "YouTube"
            if duplicate_job_id is not None
            else self.config.music_root / "YouTube"
        )
        output = str(
            output_root
            / "%(playlist,uploader)s"
            / "%(playlist_index&{} - |)s%(title)s.%(ext)s"
        )
        command = [
            self.config.yt_dlp_bin,
            "--ignore-errors",
            "--yes-playlist",
            "--extract-audio",
            "--audio-format",
            self.config.audio_format,
            "--embed-metadata",
            "--embed-thumbnail",
            "--js-runtimes",
            "deno",
        ]
        if duplicate_job_id is None:
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
            output,
            url,
            ]
        )
        return command

    def spotify_command(self, url: str, duplicate_job_id: int | None = None) -> list[str]:
        output_root = (
            self.config.music_root / "Duplicates" / str(duplicate_job_id) / "Spotify"
            if duplicate_job_id is not None
            else self.config.music_root / "Spotify"
        )
        output = str(
            output_root
            / "{album-artist}"
            / "{album}"
            / "{track-number} - {title}.{output-ext}"
        )
        return [
            self.config.spotdl_bin,
            "download",
            url,
            "--format",
            self.config.audio_format,
            "--bitrate",
            "disable",
            "--output",
            output,
        ]

    def output_root_for(self, source: Source, duplicate_job_id: int | None = None) -> Path:
        if duplicate_job_id is not None:
            return self.config.music_root / "Duplicates" / str(duplicate_job_id)
        if source == Source.YOUTUBE:
            return self.config.music_root / "YouTube"
        if source == Source.SPOTIFY:
            return self.config.music_root / "Spotify"
        return self.config.music_root
