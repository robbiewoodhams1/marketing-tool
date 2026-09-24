"""Controlled real-Supabase persistence smoke test (NOT part of the unit tests).

Fetches ONE real YouTube video (metadata, transcript, up to 5 comments) and
saves it as one new research job, then reads the rows back and checks
research_jobs -> content -> comments. It writes real rows and never deletes
anything; the job's query starts with "[smoke test]" so it is easy to find.

    python scripts/smoke_persistence.py --video-id VIDEO_ID --confirm-write

Needs YOUTUBE_API_KEY, SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY in the
environment. Run it again with the same id and it creates a second job.
"""

from __future__ import annotations

import argparse
import sys

from research.comments import YouTubeComments
from research.config import Settings
from research.logging import configure_logging
from research.metadata import YouTubeMetadata
from research.persistence import ResearchRepository, ResearchVideo, SupabaseDatabase
from research.transcripts import TranscriptUnavailableError, YouTubeTranscripts
from research.youtube import YouTubeClient

MAX_SMOKE_COMMENTS = 5


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--video-id", required=True)
    parser.add_argument("--max-comments", type=int, default=MAX_SMOKE_COMMENTS)
    parser.add_argument("--confirm-write", action="store_true",
                        help="required: acknowledge that real rows will be written")
    args = parser.parse_args()
    if not 0 <= args.max_comments <= MAX_SMOKE_COMMENTS:
        parser.error(f"--max-comments must be between 0 and {MAX_SMOKE_COMMENTS}")
    if not args.confirm_write:
        parser.error("refusing to write to Supabase without --confirm-write")

    settings = Settings.from_env()
    configure_logging(settings.log_level)
    repo = ResearchRepository.from_settings(settings)  # fails clearly if unconfigured

    client = YouTubeClient.from_settings(settings)
    [meta] = YouTubeMetadata(client).get_metadata([args.video_id]) or [None]
    if meta is None:
        print(f"Video {args.video_id} not found on YouTube")
        return 1
    try:
        transcript = YouTubeTranscripts().get_transcript(meta.video_id)
    except TranscriptUnavailableError as exc:
        print(f"No transcript ({type(exc).__name__}); content.transcript will be NULL")
        transcript = None
    comments = (
        YouTubeComments(client).get_comments([meta.video_id], max_comments_per_video=args.max_comments)
        if args.max_comments else []
    )

    saved = repo.save_result(
        f"[smoke test] {meta.video_id}", "smoke test", "Verify persistence",
        [ResearchVideo(meta, transcript, comments)],
    )
    print(f"Saved: job {saved.job_id} {saved.summary}")

    db = SupabaseDatabase(settings.supabase_url, settings.supabase_service_role_key)
    [job] = db.select("research_jobs", "id,status,completed_at", eq={"id": saved.job_id})
    content = db.select("content", "id,external_id,transcript", eq={"research_job_id": saved.job_id})
    stored = db.select("comments", "id", in_=("content_id", [c["id"] for c in content]))
    print(f"research_jobs: status={job['status']} completed_at={job['completed_at']}")
    print(f"content: {len(content)} row(s), transcript stored={bool(content and content[0]['transcript'])}")
    print(f"comments: {len(stored)} row(s)")

    ok = (job["status"] == "completed" and len(content) == 1
          and len(stored) == len(comments))
    print("SMOKE TEST", "PASSED" if ok else "FAILED")
    print(f"Inspect/clean up in Supabase: research_jobs.id = {saved.job_id}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
