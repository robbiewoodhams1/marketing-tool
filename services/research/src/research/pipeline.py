"""Run one existing, queued research job through the acquisition pipeline.

    queued -> running -> search -> metadata -> transcripts -> comments
           -> save -> completed          (any fatal error: -> failed)

This is orchestration only: every step is an existing service, injected so the
flow can be tested without YouTube or Supabase.

Concurrency: the queued -> running transition is a single conditional update
(`ResearchRepository.start_job`), so if two processes are given the same job
only one proceeds and the other gets `JobNotRunnableError`. There is no other
locking, and a process killed hard (SIGKILL, power loss) leaves its job in
"running"; there is no recovery or retry yet.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from research.comments import YouTubeComments
from research.logging import get_logger
from research.metadata import YouTubeMetadata
from research.persistence import (
    STATUS_QUEUED,
    ResearchRepository,
    ResearchVideo,
    SaveSummary,
)
from research.search import DEFAULT_MAX_CANDIDATES, YouTubeSearch
from research.transcripts import YouTubeTranscripts

log = get_logger("research.pipeline")

Progress = Callable[[str], None]


class JobError(Exception):
    """The job cannot be run. Nothing about it was changed."""


class JobNotFoundError(JobError):
    pass


class JobNotRunnableError(JobError):
    pass


@dataclass(frozen=True)
class RunSummary:
    job_id: str
    videos_found: int
    videos_with_metadata: int
    transcripts_retrieved: int
    transcripts_unavailable: int
    comments_retrieved: int
    saved: SaveSummary


def _ignore(_: str) -> None:
    pass


def run_research_job(
    job_id: str,
    *,
    repository: ResearchRepository,
    search: YouTubeSearch,
    metadata: YouTubeMetadata,
    transcripts: YouTubeTranscripts,
    comments: YouTubeComments,
    max_candidates: int = DEFAULT_MAX_CANDIDATES,
    progress: Progress = _ignore,
) -> RunSummary:
    """Execute the queued job `job_id`, saving results against that same job.

    Raises JobNotFoundError / JobNotRunnableError (before changing anything)
    if the job is missing or not "queued". Once the job is running, any
    exception marks it "failed" (completed_at stays NULL) and is re-raised;
    rows already saved remain, as persistence is not transactional.

    Videos without a transcript or comments do not fail the job. Errors the
    services treat as fatal (auth, quota, network, provider) do.
    """
    job = repository.get_job(job_id)
    if job is None:
        raise JobNotFoundError(f"Research job {job_id} does not exist")
    if job.status != STATUS_QUEUED:
        raise JobNotRunnableError(
            f"Research job {job_id} is {job.status!r}, not 'queued'; "
            "only queued jobs can be run"
        )
    query = (job.query or "").strip()
    if not query:
        raise JobNotRunnableError(f"Research job {job_id} has no query to search for")

    progress(f"Job: {job.id}")
    progress(f"Query: {query}")
    progress(f"Audience: {job.audience or '—'}")
    if not repository.start_job(job_id):
        raise JobNotRunnableError(
            f"Research job {job_id} is no longer queued (another process may have "
            "started it)"
        )
    progress("Status: queued → running")

    try:
        progress("Searching YouTube...")
        candidates = search.search(query, max_candidates=max_candidates)
        progress(f"✓ Found {len(candidates)} videos")

        progress("Fetching metadata...")
        videos = metadata.get_metadata([c.video_id for c in candidates])
        progress(f"✓ Retrieved {len(videos)} videos")
        if len(videos) < len(candidates):
            progress(f"⚠ {len(candidates) - len(videos)} unavailable")
        video_ids = [v.video_id for v in videos]

        progress("Fetching transcripts...")
        found_transcripts = {t.video_id: t for t in transcripts.get_transcripts(video_ids)}
        progress(f"✓ Retrieved {len(found_transcripts)} transcripts")
        missing = len(video_ids) - len(found_transcripts)
        if missing:
            progress(f"⚠ {missing} unavailable")

        progress("Fetching comments...")
        all_comments = comments.get_comments(video_ids)
        by_video: dict[str, list] = {}
        for comment in all_comments:
            by_video.setdefault(comment.video_id, []).append(comment)
        progress(f"✓ Retrieved {len(all_comments)} comments")

        progress("Saving results...")
        saved = repository.save_videos(
            job_id,
            [
                ResearchVideo(v, found_transcripts.get(v.video_id), by_video.get(v.video_id, []))
                for v in videos
            ],
        )
        progress(f"✓ Saved {saved.content_inserted} videos")
        progress(f"✓ Saved {saved.comments_inserted} comments")

        progress("Completing job...")
        repository.complete_job(job_id)
        progress("✓ Job completed")
    except BaseException as exc:  # includes Ctrl-C: never leave the job "running"
        log.error("Research job %s failed: %s: %s", job_id, type(exc).__name__, exc)
        try:
            repository.fail_job(job_id)
            progress("Status: running → failed")
        except Exception as fail_exc:
            log.error("Could not mark job %s failed: %s", job_id, fail_exc)
        raise

    return RunSummary(
        job_id=job_id,
        videos_found=len(candidates),
        videos_with_metadata=len(videos),
        transcripts_retrieved=len(found_transcripts),
        transcripts_unavailable=missing,
        comments_retrieved=len(all_comments),
        saved=saved,
    )
