from __future__ import annotations

import argparse
import sys

from media_dl.config import load_config
from media_dl.db import Database
from media_dl.queue import import_queue
from media_dl.urltools import Source, classify_url
from media_dl.worker import process_job, run_once, watch


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

    args = parser.parse_args(argv)
    config = load_config()
    config.ensure_dirs()

    if args.command == "watch":
        watch(config)
        return 0

    db = Database(config.db_path)
    try:
        if args.command == "add":
            classified = classify_url(args.url)
            if classified.source == Source.UNKNOWN:
                print(f"unsupported url: {args.url}", file=sys.stderr)
                return 2
            job, created = db.add_job(
                classified.source,
                classified.raw_url,
                classified.normalized_url,
            )
            verb = "queued" if created else "duplicate"
            print(f"{verb}: job {job.id} {job.source.value} {job.normalized_url}")
            if args.queue_only or job.status not in {"queued", "retry"}:
                return 0

            db.close()
            processed = process_job(config, job.id)
            print(f"processed: job {processed.id} {processed.status}")
            return 1 if processed.status == "failed" else 0

        if args.command == "import-queue":
            results = import_queue(config.queue_dir, db)
            for job, created, error in results:
                if error:
                    print(error, file=sys.stderr)
                elif job:
                    verb = "queued" if created else "duplicate"
                    print(f"{verb}: job {job.id} {job.source.value} {job.normalized_url}")
            return 0

        if args.command == "run-once":
            db.close()
            processed = run_once(config, max_jobs=args.max_jobs)
            print(f"processed {processed} job(s)")
            return 0

        if args.command == "status":
            for job in db.list_jobs(limit=args.limit):
                error = f" error={job.last_error}" if job.last_error else ""
                print(
                    f"{job.id:4} {job.source.value:7} {job.status:9} "
                    f"attempts={job.attempts} {job.normalized_url}{error}"
                )
            return 0

        if args.command == "retry":
            job = db.retry(args.job_id)
            print(f"retry queued: job {job.id}")
            if args.queue_only:
                return 0

            db.close()
            processed = process_job(config, job.id)
            print(f"processed: job {processed.id} {processed.status}")
            return 1 if processed.status == "failed" else 0

        if args.command == "skip":
            job = db.skip(args.job_id, args.reason)
            print(f"skipped: job {job.id}")
            return 0
    finally:
        try:
            db.close()
        except Exception:
            pass

    parser.error(f"unknown command {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
