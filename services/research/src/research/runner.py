"""Terminal entry point: run one queued research job.

    python -m research.runner --job-id JOB_ID

Needs YOUTUBE_API_KEY, SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY in the
environment. Exit codes: 0 completed, 1 job could not run / not runnable,
2 bad arguments or missing configuration, 3 the run failed (job marked failed).
"""

from __future__ import annotations

import argparse
import sys
import uuid
from collections.abc import Sequence

from research.comments import YouTubeComments
from research.config import Settings
from research.logging import configure_logging, get_logger
from research.metadata import YouTubeMetadata
from research.persistence import PersistenceConfigError, PersistenceError, ResearchRepository
from research.pipeline import JobError, run_research_job
from research.search import YouTubeSearch
from research.transcripts import YouTubeTranscripts
from research.youtube import YouTubeClient

log = get_logger("research.runner")

EXIT_OK, EXIT_JOB, EXIT_CONFIG, EXIT_FAILED = 0, 1, 2, 3


def _say(message: str) -> None:
    print(message, flush=True)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m research.runner", description="Run one queued research job."
    )
    parser.add_argument("--job-id", required=True, help="id of a research_jobs row")
    args = parser.parse_args(argv)

    try:
        job_id = str(uuid.UUID(args.job_id))
    except ValueError:
        print(f"error: --job-id must be a UUID, got {args.job_id!r}", file=sys.stderr)
        return EXIT_CONFIG

    settings = Settings.from_env()
    configure_logging(settings.log_level)
    if not settings.youtube_api_key:
        print("error: YOUTUBE_API_KEY is not set", file=sys.stderr)
        return EXIT_CONFIG
    try:
        repository = ResearchRepository.from_settings(settings)
    except PersistenceConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_CONFIG

    client = YouTubeClient.from_settings(settings)
    _say("Research Runner")
    _say("────────────────────────────────")
    try:
        summary = run_research_job(
            job_id,
            repository=repository,
            search=YouTubeSearch(client),
            metadata=YouTubeMetadata(client),
            transcripts=YouTubeTranscripts(),
            comments=YouTubeComments(client),
            progress=lambda line: _say(f"\n{line}" if line.endswith("...") else line),
        )
    except JobError as exc:
        print(f"\nCannot run job: {exc}", file=sys.stderr)
        return EXIT_JOB
    except (Exception, KeyboardInterrupt) as exc:
        print(f"\nResearch failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_FAILED

    _say(f"\nResearch complete: {summary.saved.content_inserted} videos, "
         f"{summary.saved.comments_inserted} comments saved to job {job_id}.")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
