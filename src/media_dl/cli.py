from __future__ import annotations

import argparse
import sys

from media_dl.config import load_config
from media_dl.jobs import (
    UnsupportedUrlError,
    add_url,
    import_queue_files,
    list_recent_jobs,
    retry_job,
    run_queued_jobs,
    skip_job,
)
from media_dl.worker import watch


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="media-dl")
    sub = parser.add_subparsers(dest="command", required=True)

    add = sub.add_parser("add", help="add a YouTube or Spotify URL")
    add.add_argument("url")
    add.add_argument(
        "--queue-only",
        action="store_true",
        help="queue the URL without immediately processing it",
    )
    add.add_argument(
        "--allow-duplicate",
        action="store_true",
        help="create a new job even if this URL was already submitted",
    )

    sub.add_parser("import-queue", help="import watched queue files without running jobs")

    run_once_parser = sub.add_parser("run-once", help="import queue and process queued jobs")
    run_once_parser.add_argument("--max-jobs", type=int)

    status = sub.add_parser("status", help="show recent jobs")
    status.add_argument("--limit", type=int, default=25)

    retry = sub.add_parser("retry", help="mark a failed job for retry")
    retry.add_argument("job_id", type=int)
    retry.add_argument(
        "--queue-only",
        action="store_true",
        help="mark the job for retry without immediately processing it",
    )

    skip = sub.add_parser("skip", help="mark a bad job as skipped")
    skip.add_argument("job_id", type=int)
    skip.add_argument("--reason", default="manually skipped")

    sub.add_parser("watch", help="poll queue files and process jobs")
    sub.add_parser("serve", help="serve localhost API")

    args = parser.parse_args(argv)
    config = load_config()
    config.ensure_dirs()

    if args.command == "watch":
        watch(config)
        return 0

    if args.command == "serve":
        from media_dl.service import serve

        serve(config)
        return 0

    if args.command == "add":
        try:
            result = add_url(
                config,
                args.url,
                queue_only=args.queue_only,
                allow_duplicate=args.allow_duplicate,
            )
        except UnsupportedUrlError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        verb = "queued" if result.created else "duplicate"
        if result.created and result.job.allow_duplicate:
            verb = "queued duplicate"
        print(f"{verb}: job {result.job.id} {result.job.source.value} {result.job.normalized_url}")
        if result.processed:
            print(f"processed: job {result.processed.id} {result.processed.status}")
            return 1 if result.processed.status == "failed" else 0
        return 0

    if args.command == "import-queue":
        results = import_queue_files(config)
        for job, created, error in results:
            if error:
                print(error, file=sys.stderr)
            elif job:
                verb = "queued" if created else "duplicate"
                print(f"{verb}: job {job.id} {job.source.value} {job.normalized_url}")
        return 0

    if args.command == "run-once":
        processed = run_queued_jobs(config, max_jobs=args.max_jobs)
        print(f"processed {processed} job(s)")
        return 0

    if args.command == "status":
        for job in list_recent_jobs(config, limit=args.limit).jobs:
            error = f" error={job.last_error}" if job.last_error else ""
            warning = f" warning={job.last_warning}" if job.last_warning else ""
            duplicate = (
                f" duplicate_of={job.duplicate_of}"
                if job.allow_duplicate and job.duplicate_of is not None
                else ""
            )
            print(
                f"{job.id:4} {job.source.value:7} {job.status:9} "
                f"attempts={job.attempts}{duplicate} {job.normalized_url}{error}{warning}"
            )
        return 0

    if args.command == "retry":
        result = retry_job(config, args.job_id, queue_only=args.queue_only)
        print(f"retry queued: job {result.job.id}")
        if result.processed:
            print(f"processed: job {result.processed.id} {result.processed.status}")
            return 1 if result.processed.status == "failed" else 0
        return 0

    if args.command == "skip":
        job = skip_job(config, args.job_id, args.reason)
        print(f"skipped: job {job.id}")
        return 0

    parser.error(f"unknown command {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
