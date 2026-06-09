from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from media_dl.config import Config
from media_dl.db import Job
from media_dl.urltools import Source


OUTPUT_LAYOUT_SOURCE_FOLDERS = "source_folders"
OUTPUT_LAYOUT_CREATOR_FOLDERS = "creator_folders"
OUTPUT_LAYOUT_ARTIST_ALBUM_FOLDERS = "artist_album_folders"
ALLOWED_OUTPUT_LAYOUTS: tuple[str, ...] = (
    OUTPUT_LAYOUT_SOURCE_FOLDERS,
    OUTPUT_LAYOUT_CREATOR_FOLDERS,
    OUTPUT_LAYOUT_ARTIST_ALBUM_FOLDERS,
)
DEFAULT_OUTPUT_LAYOUT = OUTPUT_LAYOUT_SOURCE_FOLDERS

METADATA_MODE_SOURCE = "source"
METADATA_MODE_NAVIDROME_CLEAN = "navidrome_clean"
ALLOWED_METADATA_MODES: tuple[str, ...] = (
    METADATA_MODE_SOURCE,
    METADATA_MODE_NAVIDROME_CLEAN,
)
DEFAULT_METADATA_MODE = METADATA_MODE_SOURCE

PLAYLIST_MODE_SINGLE_JOB = "single_job"
PLAYLIST_MODE_EXPAND_ITEMS = "expand_items"
ALLOWED_PLAYLIST_MODES: tuple[str, ...] = (
    PLAYLIST_MODE_SINGLE_JOB,
    PLAYLIST_MODE_EXPAND_ITEMS,
)
DEFAULT_PLAYLIST_MODE = PLAYLIST_MODE_SINGLE_JOB

COLLISION_SUFFIX_JOB_ID = "job_id"


class DownloadSettings(Protocol):
    audio_format: str
    thumbnail_mode: str
    output_layout: str
    metadata_mode: str
    playlist_mode: str


@dataclass(frozen=True)
class OutputLayout:
    name: str
    output_root: Path
    output_template: str
    effective_layout: str


@dataclass(frozen=True)
class MetadataPolicy:
    mode: str
    album_artist_fallback: str = "copy_artist"
    multi_artist_policy: str = "preserve_list"
    playlist_track_numbers: bool = True


@dataclass(frozen=True)
class PlaylistPolicy:
    mode: str
    parent_behavior: str = "import_summary"


@dataclass(frozen=True)
class DownloadPlan:
    source: Source
    url: str
    duplicate_job_id: int | None
    audio_format: str
    thumbnail_mode: str
    output_layout: OutputLayout
    metadata_policy: MetadataPolicy
    playlist_policy: PlaylistPolicy
    use_archive: bool
    collision_suffix: str = COLLISION_SUFFIX_JOB_ID


def plan_download(config: Config, job: Job, settings: DownloadSettings) -> DownloadPlan:
    duplicate_job_id = job.id if job.allow_duplicate else None
    layout = _output_layout(
        config=config,
        source=job.source,
        requested_layout=settings.output_layout,
        duplicate_job_id=duplicate_job_id,
    )
    return DownloadPlan(
        source=job.source,
        url=job.normalized_url,
        duplicate_job_id=duplicate_job_id,
        audio_format=settings.audio_format,
        thumbnail_mode=settings.thumbnail_mode,
        output_layout=layout,
        metadata_policy=MetadataPolicy(settings.metadata_mode),
        playlist_policy=PlaylistPolicy(settings.playlist_mode),
        use_archive=job.source == Source.YOUTUBE and duplicate_job_id is None,
    )


def creator_path(root: Path, creator: str, title: str, extension: str) -> Path:
    return root / sanitize_path_part(creator or "Unknown Artist") / _file_name(title, extension)


def artist_album_path(
    root: Path,
    artist: str | None,
    album: str | None,
    title: str,
    extension: str,
    track_number: str | int | None = None,
    creator: str | None = None,
) -> Path:
    clean_artist = sanitize_path_part(artist or creator or "Unknown Artist")
    clean_title = sanitize_path_part(title or "Unknown Title")
    if not album:
        return root / clean_artist / _file_name(clean_title, extension)

    clean_album = sanitize_path_part(album)
    prefix = f"{track_number} - " if track_number not in (None, "") else ""
    return root / clean_artist / clean_album / _file_name(f"{prefix}{clean_title}", extension)


def collision_path(path: Path, job_id: int) -> Path:
    return path.with_name(f"{path.stem} [job-{job_id}]{path.suffix}")


def sanitize_path_part(value: str) -> str:
    cleaned = re.sub(r"[\x00-\x1f/\\]+", " ", value)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or "Unknown Artist"


def _output_layout(
    config: Config,
    source: Source,
    requested_layout: str,
    duplicate_job_id: int | None,
) -> OutputLayout:
    if duplicate_job_id is not None:
        return _duplicate_layout(config, source, duplicate_job_id, requested_layout)
    if requested_layout == OUTPUT_LAYOUT_CREATOR_FOLDERS:
        return _creator_layout(config, source, requested_layout)
    if requested_layout == OUTPUT_LAYOUT_ARTIST_ALBUM_FOLDERS:
        return _artist_album_layout(config, source, requested_layout)
    return _source_layout(config, source)


def _source_layout(config: Config, source: Source) -> OutputLayout:
    if source == Source.YOUTUBE:
        output_root = config.music_root / "YouTube"
        template = str(
            output_root
            / "%(playlist,uploader)s"
            / "%(playlist_index&{} - |)s%(title)s.%(ext)s"
        )
        return OutputLayout(
            name=OUTPUT_LAYOUT_SOURCE_FOLDERS,
            output_root=output_root,
            output_template=template,
            effective_layout=OUTPUT_LAYOUT_SOURCE_FOLDERS,
        )
    if source == Source.SPOTIFY:
        output_root = config.music_root / "Spotify"
        template = str(
            output_root
            / "{album-artist}"
            / "{album}"
            / "{track-number} - {title}.{output-ext}"
        )
        return OutputLayout(
            name=OUTPUT_LAYOUT_SOURCE_FOLDERS,
            output_root=output_root,
            output_template=template,
            effective_layout=OUTPUT_LAYOUT_SOURCE_FOLDERS,
        )
    return OutputLayout(
        name=OUTPUT_LAYOUT_SOURCE_FOLDERS,
        output_root=config.music_root,
        output_template=str(config.music_root / "{title}.{output-ext}"),
        effective_layout=OUTPUT_LAYOUT_SOURCE_FOLDERS,
    )


def _creator_layout(config: Config, source: Source, requested_layout: str) -> OutputLayout:
    if source == Source.YOUTUBE:
        template = str(
            config.music_root
            / "%(uploader,channel|Unknown Artist)s"
            / "%(title)s.%(ext)s"
        )
    elif source == Source.SPOTIFY:
        template = str(config.music_root / "{artist}" / "{title}.{output-ext}")
    else:
        template = str(config.music_root / "{title}.{output-ext}")
    return OutputLayout(
        name=requested_layout,
        output_root=config.music_root,
        output_template=template,
        effective_layout=OUTPUT_LAYOUT_CREATOR_FOLDERS,
    )


def _artist_album_layout(config: Config, source: Source, requested_layout: str) -> OutputLayout:
    if source == Source.SPOTIFY:
        template = str(
            config.music_root
            / "{artist}"
            / "{album}"
            / "{track-number} - {title}.{output-ext}"
        )
        return OutputLayout(
            name=requested_layout,
            output_root=config.music_root,
            output_template=template,
            effective_layout=OUTPUT_LAYOUT_ARTIST_ALBUM_FOLDERS,
        )
    return _creator_layout(config, source, requested_layout)


def _duplicate_layout(
    config: Config,
    source: Source,
    duplicate_job_id: int,
    requested_layout: str,
) -> OutputLayout:
    source_name = "YouTube" if source == Source.YOUTUBE else "Spotify"
    output_root = config.music_root / "Duplicates" / str(duplicate_job_id) / source_name
    if source == Source.YOUTUBE:
        template = str(
            output_root
            / "%(playlist,uploader)s"
            / "%(playlist_index&{} - |)s%(title)s.%(ext)s"
        )
    else:
        template = str(
            output_root
            / "{album-artist}"
            / "{album}"
            / "{track-number} - {title}.{output-ext}"
        )
    return OutputLayout(
        name=requested_layout,
        output_root=output_root,
        output_template=template,
        effective_layout=requested_layout,
    )


def _file_name(stem: str, extension: str) -> str:
    suffix = extension if extension.startswith(".") else f".{extension}"
    return f"{sanitize_path_part(stem)}{suffix}"
