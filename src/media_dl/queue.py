"""Import simple text queue files into the shared job database."""

from __future__ import annotations

from pathlib import Path

from media_dl.db import Database, Job
from media_dl.urltools import Source, classify_url


QUEUE_FILES = ("inbox.txt", "youtube.txt", "spotify.txt")


def ensure_queue_files(queue_dir: Path) -> None:
    """Create the queue directory and standard queue files if needed."""
    queue_dir.mkdir(parents=True, exist_ok=True)
    for name in QUEUE_FILES:
        (queue_dir / name).touch(exist_ok=True)


def import_queue(queue_dir: Path, db: Database) -> list[tuple[Job | None, bool, str | None]]:
    """Read all queue files and add supported URLs as jobs."""
    ensure_queue_files(queue_dir)
    results: list[tuple[Job | None, bool, str | None]] = []

    for path in [queue_dir / name for name in QUEUE_FILES]:
        for line in _read_lines(path):
            classified = classify_url(line)
            if classified.source == Source.UNKNOWN:
                results.append((None, False, f"unsupported url: {line}"))
                continue

            job, created = db.add_job(
                classified.source,
                classified.raw_url,
                classified.normalized_url,
            )
            results.append((job, created, None))

    return results


def _read_lines(path: Path) -> list[str]:
    """Return non-empty, non-comment lines from a queue file."""
    if not path.exists():
        return []
    urls: list[str] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        urls.append(line)
    return urls
