"""Persist user-adjustable download policies in the state directory."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from media_dl.config import Config
from media_dl.download_plan import (
    ALLOWED_METADATA_MODES,
    ALLOWED_OUTPUT_LAYOUTS,
    ALLOWED_PLAYLIST_MODES,
    DEFAULT_METADATA_MODE,
    DEFAULT_OUTPUT_LAYOUT,
    PLAYLIST_MODE_SINGLE_JOB,
)


ALLOWED_AUDIO_FORMATS: tuple[str, ...] = ("m4a", "mp3", "flac", "opus", "wav")
ALLOWED_THUMBNAIL_MODES: tuple[str, ...] = ("source", "default", "none")
DEFAULT_AUDIO_FORMAT = "m4a"
DEFAULT_THUMBNAIL_MODE = "source"
DEFAULT_PLAYLIST_MODE = PLAYLIST_MODE_SINGLE_JOB
SETTINGS_FILENAME = "runtime-settings.json"


@dataclass(frozen=True)
class RuntimeSettings:
    """Capture settings that override static environment defaults at runtime."""

    audio_format: str
    thumbnail_mode: str
    output_layout: str
    metadata_mode: str
    playlist_mode: str


def runtime_settings_path(config: Config) -> Path:
    """Return the JSON settings path under the state mount."""
    return config.state_dir / SETTINGS_FILENAME


def load_runtime_settings(config: Config) -> RuntimeSettings:
    """Load persisted settings, falling back to valid defaults for missing values."""
    config.ensure_dirs()
    path = runtime_settings_path(config)
    persisted = _read_persisted_settings(path)

    audio_format = persisted.get("audio_format") or (
        config.audio_format if config.audio_format in ALLOWED_AUDIO_FORMATS else DEFAULT_AUDIO_FORMAT
    )
    thumbnail_mode = persisted.get("thumbnail_mode") or DEFAULT_THUMBNAIL_MODE
    output_layout = persisted.get("output_layout") or DEFAULT_OUTPUT_LAYOUT
    metadata_mode = persisted.get("metadata_mode") or DEFAULT_METADATA_MODE
    playlist_mode = persisted.get("playlist_mode") or DEFAULT_PLAYLIST_MODE
    return RuntimeSettings(
        audio_format=audio_format,
        thumbnail_mode=thumbnail_mode,
        output_layout=output_layout,
        metadata_mode=metadata_mode,
        playlist_mode=playlist_mode,
    )


def save_runtime_settings(
    config: Config,
    audio_format: str,
    thumbnail_mode: str = DEFAULT_THUMBNAIL_MODE,
    output_layout: str = DEFAULT_OUTPUT_LAYOUT,
    metadata_mode: str = DEFAULT_METADATA_MODE,
    playlist_mode: str = DEFAULT_PLAYLIST_MODE,
) -> RuntimeSettings:
    """Validate and persist all runtime settings atomically as JSON text."""
    if audio_format not in ALLOWED_AUDIO_FORMATS:
        raise ValueError(f"unsupported audio format: {audio_format}")
    if thumbnail_mode not in ALLOWED_THUMBNAIL_MODES:
        raise ValueError(f"unsupported thumbnail mode: {thumbnail_mode}")
    if output_layout not in ALLOWED_OUTPUT_LAYOUTS:
        raise ValueError(f"unsupported output layout: {output_layout}")
    if metadata_mode not in ALLOWED_METADATA_MODES:
        raise ValueError(f"unsupported metadata mode: {metadata_mode}")
    if playlist_mode not in ALLOWED_PLAYLIST_MODES:
        raise ValueError(f"unsupported playlist mode: {playlist_mode}")

    config.ensure_dirs()
    path = runtime_settings_path(config)
    path.write_text(
        json.dumps(
            {
                "audio_format": audio_format,
                "metadata_mode": metadata_mode,
                "output_layout": output_layout,
                "playlist_mode": playlist_mode,
                "thumbnail_mode": thumbnail_mode,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return RuntimeSettings(
        audio_format=audio_format,
        thumbnail_mode=thumbnail_mode,
        output_layout=output_layout,
        metadata_mode=metadata_mode,
        playlist_mode=playlist_mode,
    )


def _read_persisted_settings(path: Path) -> dict[str, str]:
    """Read only recognized and currently allowed settings from disk."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    if not isinstance(raw, dict):
        return {}

    settings: dict[str, str] = {}

    audio_format = raw.get("audio_format")
    if isinstance(audio_format, str) and audio_format in ALLOWED_AUDIO_FORMATS:
        settings["audio_format"] = audio_format

    thumbnail_mode = raw.get("thumbnail_mode")
    if isinstance(thumbnail_mode, str) and thumbnail_mode in ALLOWED_THUMBNAIL_MODES:
        settings["thumbnail_mode"] = thumbnail_mode

    output_layout = raw.get("output_layout")
    if isinstance(output_layout, str) and output_layout in ALLOWED_OUTPUT_LAYOUTS:
        settings["output_layout"] = output_layout

    metadata_mode = raw.get("metadata_mode")
    if isinstance(metadata_mode, str) and metadata_mode in ALLOWED_METADATA_MODES:
        settings["metadata_mode"] = metadata_mode

    playlist_mode = raw.get("playlist_mode")
    if isinstance(playlist_mode, str) and playlist_mode in ALLOWED_PLAYLIST_MODES:
        settings["playlist_mode"] = playlist_mode

    return settings
