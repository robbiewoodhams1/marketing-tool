"""Research-specific YouTube search.

`youtube.py` knows how to call YouTube; this module decides how the research
system searches it: validating the research query, applying the candidate
limit, and returning de-duplicated candidate videos in YouTube's order.
"""

from __future__ import annotations

from datetime import datetime

from research.logging import get_logger
from research.youtube import MAX_RESULTS_LIMIT, YouTubeClient, YouTubeSearchResult

log = get_logger("research.search")

MAX_QUERY_LENGTH = 500
DEFAULT_MAX_CANDIDATES = 10


class YouTubeSearch:
    def __init__(self, client: YouTubeClient):
        self._client = client

    def search(
        self,
        query: str,
        *,
        max_candidates: int = DEFAULT_MAX_CANDIDATES,
        published_after: datetime | None = None,
        published_before: datetime | None = None,
    ) -> list[YouTubeSearchResult]:
        """Return candidate videos for a research query.

        Raises ValueError for an invalid query or candidate limit. Errors from
        the YouTube client (`YouTubeError` subclasses) propagate unchanged.
        Pagination is not implemented, so more than 50 candidates is rejected.
        """
        query = query.strip()
        if not query:
            raise ValueError("query must not be empty")
        if len(query) > MAX_QUERY_LENGTH:
            raise ValueError(f"query must be at most {MAX_QUERY_LENGTH} characters")
        if not 1 <= max_candidates <= MAX_RESULTS_LIMIT:
            raise ValueError(
                f"max_candidates must be between 1 and {MAX_RESULTS_LIMIT} "
                "(pagination is not supported)"
            )

        log.info("Search started: query=%r max_candidates=%d", query, max_candidates)
        found = self._client.search_videos(
            query,
            max_results=max_candidates,
            published_after=published_after,
            published_before=published_before,
        )

        seen: set[str] = set()
        candidates = []
        for video in found:
            if video.video_id not in seen:
                seen.add(video.video_id)
                candidates.append(video)

        log.info(
            "Search completed: %d candidates (%d duplicates removed)",
            len(candidates),
            len(found) - len(candidates),
        )
        return candidates
