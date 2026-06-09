from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    music_root: Path
    state_dir: Path
    queue_dir: Path
    download_dir: Path
    config_dir: Path | None = None
    default_thumbnail_path: Path | None = None
    audio_format: str = "m4a"
    thumbnail_mode: str = "source"
    poll_seconds: int = 5
    yt_dlp_bin: str = "yt-dlp"
    spotdl_bin: str = "spotdl"
    service_port: int = 8765

    @property
    def db_path(self) -> Path:
        return self.state_dir / "media-dl.sqlite"

    @property
    def yt_archive_path(self) -> Path:
        return self.state_dir / "yt-dlp.archive"

    @property
    def tmp_dir(self) -> Path:
        return self.download_dir / "tmp"

    @property
    def resolved_config_dir(self) -> Path:
        return self.config_dir or self.state_dir

    @property
    def resolved_default_thumbnail_path(self) -> Path:
        return self.default_thumbnail_path or (self.resolved_config_dir / "default.jpg")

    def ensure_dirs(self) -> None:
        for path in (
            self.music_root,
            self.state_dir,
            self.queue_dir,
            self.download_dir,
            self.resolved_config_dir,
            self.tmp_dir,
            self.music_root / "YouTube",
            self.music_root / "Spotify",
        ):
            path.mkdir(parents=True, exist_ok=True)


def load_config() -> Config:
    return Config(
        music_root=Path(os.getenv("MUSIC_ROOT", "./music")).resolve(),
        state_dir=Path(os.getenv("STATE_DIR", "./state")).resolve(),
        queue_dir=Path(os.getenv("QUEUE_DIR", "./queue")).resolve(),
        download_dir=Path(os.getenv("DOWNLOAD_DIR", "./downloads")).resolve(),
        config_dir=Path(os.getenv("CONFIG_DIR", "./config")).resolve(),
        default_thumbnail_path=Path(
            os.getenv("DEFAULT_THUMBNAIL_PATH", "./config/default.jpg")
        ).resolve(),
        audio_format=os.getenv("AUDIO_FORMAT", "m4a"),
        poll_seconds=int(os.getenv("POLL_SECONDS", "5")),
        yt_dlp_bin=os.getenv("YT_DLP_BIN", "yt-dlp"),
        spotdl_bin=os.getenv("SPOTDL_BIN", "spotdl"),
        service_port=int(os.getenv("SERVICE_PORT", "8765")),
    )
