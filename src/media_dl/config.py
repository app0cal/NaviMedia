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
    audio_format: str = "m4a"
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

    def ensure_dirs(self) -> None:
        for path in (
            self.music_root,
            self.state_dir,
            self.queue_dir,
            self.download_dir,
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
        audio_format=os.getenv("AUDIO_FORMAT", "m4a"),
        poll_seconds=int(os.getenv("POLL_SECONDS", "5")),
        yt_dlp_bin=os.getenv("YT_DLP_BIN", "yt-dlp"),
        spotdl_bin=os.getenv("SPOTDL_BIN", "spotdl"),
        service_port=int(os.getenv("SERVICE_PORT", "8765")),
    )
