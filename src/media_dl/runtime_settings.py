from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from media_dl.config import Config


ALLOWED_AUDIO_FORMATS: tuple[str, ...] = ("m4a", "mp3", "flac", "opus", "wav")
DEFAULT_AUDIO_FORMAT = "m4a"
SETTINGS_FILENAME = "runtime-settings.json"


@dataclass(frozen=True)
class RuntimeSettings:
    audio_format: str


def runtime_settings_path(config: Config) -> Path:
    return config.state_dir / SETTINGS_FILENAME


def load_runtime_settings(config: Config) -> RuntimeSettings:
    config.ensure_dirs()
    path = runtime_settings_path(config)
    persisted = _read_persisted_audio_format(path)
    if persisted:
        return RuntimeSettings(audio_format=persisted)

    fallback = config.audio_format if config.audio_format in ALLOWED_AUDIO_FORMATS else DEFAULT_AUDIO_FORMAT
    return RuntimeSettings(audio_format=fallback)


def save_runtime_settings(config: Config, audio_format: str) -> RuntimeSettings:
    if audio_format not in ALLOWED_AUDIO_FORMATS:
        raise ValueError(f"unsupported audio format: {audio_format}")

    config.ensure_dirs()
    path = runtime_settings_path(config)
    path.write_text(
        json.dumps({"audio_format": audio_format}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return RuntimeSettings(audio_format=audio_format)


def _read_persisted_audio_format(path: Path) -> str | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None

    audio_format = raw.get("audio_format") if isinstance(raw, dict) else None
    if isinstance(audio_format, str) and audio_format in ALLOWED_AUDIO_FORMATS:
        return audio_format
    return None
