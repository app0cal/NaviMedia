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
            return self.youtube_command(job.normalized_url)
        if job.source == Source.SPOTIFY:
            return self.spotify_command(job.normalized_url)
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

        return DownloadResult(output_path=self.output_root_for(job.source))

    def youtube_command(self, url: str) -> list[str]:
        output = str(
            self.config.music_root
            / "YouTube"
            / "%(playlist,uploader)s"
            / "%(playlist_index&{} - |)s%(title)s.%(ext)s"
        )
        return [
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
            "--download-archive",
            str(self.config.yt_archive_path),
            "--paths",
            f"temp:{self.config.tmp_dir}",
            "--output",
            output,
            url,
        ]

    def spotify_command(self, url: str) -> list[str]:
        output = str(
            self.config.music_root
            / "Spotify"
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

    def output_root_for(self, source: Source) -> Path:
        if source == Source.YOUTUBE:
            return self.config.music_root / "YouTube"
        if source == Source.SPOTIFY:
            return self.config.music_root / "Spotify"
        return self.config.music_root
